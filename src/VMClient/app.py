from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, func, Column, Integer, String, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
import subprocess
import os
import requests
import shutil
import json
import time
import socket

# Database setup
DATABASE_URL = "sqlite:///./vm.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Table definition
class VM(Base):
    __tablename__ = "vms"
    id = Column(Integer, primary_key=True, autoincrement=True)
    identifier = Column(Integer, index=True)
    user_id = Column(Integer, index=True)
    kernel_image = Column(String)
    rootfs_image = Column(String)
    cpu = Column(Integer)
    ram = Column(Integer)
    storage = Column(String)
    socket_path = Column(String)
    port = Column(Integer, unique=True, autoincrement=True)
    ip_addr = Column(String)
    hostname = Column(String)
    gateway = Column(String)
    vm_iface = Column(String)

Base.metadata.create_all(bind=engine)

# FastAPI setup
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Modifiez en fonction de vos besoins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TMP_DIR = "/tmp/firecracker_sockets"
BASE_DIR = "/tmp/vms"
SOCKET_PREFIX = "firecracker"
used_ports = set()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Pydantic models
class VMTemplate(BaseModel):
    cpu: int
    ram: int
    storage: str
    kernel_image: str
    rootfs_image: str

class VMCreateRequest(BaseModel):
    user_id: int
    identifier: int
    ip_addr: str
    hostname: str
    gateway: str
    ssh_key: str
    template: VMTemplate

def get_next_port(db: Session, start_port: int = 9000):
    # Trouve le plus grand port utilisé
    result = db.query(func.max(VM.port)).scalar()
    if result is None:
        return start_port
    return result + 1

def get_next_vm_iface(db: Session, start: int = 0):
    # Trouve le plus grand numero utilisé
    result = db.query(func.max(VM.port)).scalar()
    if result is None:
        return f"tap{start}"
    return f"tap{result + 1}"

@app.post("/create_vm")
async def create_vm(request: VMCreateRequest, db: Session = Depends(get_db)):
    try:
        print(f"[DEBUG] Début de création de VM pour user {request.user_id}")
        user_dir = os.path.join(BASE_DIR, f"vm_for_users_{request.user_id}")
        print(f"[DEBUG] Répertoire utilisateur : {user_dir}")

        if not os.path.exists(user_dir):
            os.makedirs(user_dir)
            print(f"[DEBUG] Création du répertoire utilisateur")

        # Création de la VM
        new_vm = VM(
            user_id=request.user_id,
            identifier=request.identifier,
            kernel_image=request.template.kernel_image,
            rootfs_image=request.template.rootfs_image,
            cpu=request.template.cpu,
            ram=request.template.ram,
            storage=request.template.storage,
            socket_path="", 
            ip_addr=request.ip_addr,
            hostname=request.hostname,
            gateway=request.gateway,
            port= get_next_port(db),
            vm_iface = get_next_vm_iface(db)
        )
        print(f"[DEBUG] VM créée avec port {new_vm.port}")
        
        db.add(new_vm)
        db.flush()
        print(f"[DEBUG] VM ID obtenu : {new_vm.id}")

        # Construction du socket_path
        # Créer le dossier pour les sockets s'il n'existe pas
        if not os.path.exists(TMP_DIR):
            os.makedirs(TMP_DIR, mode=0o755)
            print(f"[DEBUG] Création du dossier pour les sockets : {TMP_DIR}")
        
        # Nettoyer le socket s'il existe déjà
        socket_path = os.path.join(TMP_DIR, f"{SOCKET_PREFIX}_{new_vm.id}.sock")
        if os.path.exists(socket_path):
            os.remove(socket_path)
            print(f"[DEBUG] Ancien socket supprimé : {socket_path}")
        print(f"[DEBUG] Socket path généré : {socket_path}")
        new_vm.socket_path = socket_path

        db.commit()
        print(f"[DEBUG] Commit effectué")

        # Création du répertoire VM
        vm_dir = os.path.join(user_dir, f"vm_{new_vm.id}")
        print(f"[DEBUG] Répertoire VM : {vm_dir}")
        if not os.path.exists(vm_dir):
            os.makedirs(vm_dir)

        # Chemins des fichiers
        kernel_path = os.path.join(vm_dir, os.path.basename(request.template.kernel_image))
        rootfs_path = os.path.join(vm_dir, os.path.basename(request.template.rootfs_image))
        print(f"[DEBUG] Kernel path : {kernel_path}")
        print(f"[DEBUG] Rootfs path : {rootfs_path}")

        # Copie des fichiers
        shutil.copy(request.template.kernel_image, kernel_path)
        shutil.copy(request.template.rootfs_image, rootfs_path)
        print(f"[DEBUG] Fichiers copiés")

        #Configuration du réseau pour la microVM
        print("[DEBUG] Configuration du réseau")
        subprocess.run(["sudo", "bash", "setup_tap.sh", 
                        new_vm.vm_iface,"br0"],
            check=True,  # Lève une exception si le code de retour n'est pas 0
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True)
        
        print("[DEBUG] Configuration du réseau et de l'espace de stockage")
        subprocess.run(["sudo", "bash", "create_rootfs.sh", 
                        rootfs_path, 
                        f"{vm_dir}/disk.ext4", 
                        str(new_vm.storage), 
                        request.ip_addr, # 192.168.5.8/24
                        request.gateway,
                        request.hostname,
                        request.ssh_key],
            check=True,  # Lève une exception si le code de retour n'est pas 0
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True)

        # Configuration
        config_path = os.path.join(vm_dir, "vm_config.json")
        print(f"[DEBUG] Chemin config : {config_path}")
        
        vm_config = {
            "boot-source": {
                "kernel_image_path": str(kernel_path),  # Conversion explicite en string
                "boot_args": "console=ttyS0 reboot=k panic=1 pci=off"
            },"network-interfaces": [
                {
                    "iface_id": "eth0",
                    "host_dev_name": new_vm.vm_iface
                }
            ],
            "drives": [
                {
                    "drive_id": "rootfs",
                    "path_on_host": str(f"{vm_dir}/disk.ext4"),#str(rootfs_path),  # Conversion explicite en string
                    "is_root_device": True,
                    "is_read_only": False
                }
            ],
            "machine-config": {
                "vcpu_count": request.template.cpu,
                "mem_size_mib": request.template.ram
            }
        }

        with open(config_path, "w") as config_file:
            json.dump(vm_config, config_file, indent=4)
        print("[DEBUG] Configuration écrite")

        print(f"[DEBUG] Lancement de Firecracker avec socket : {new_vm.socket_path}")
        firecracker_process = subprocess.Popen([
            "firecracker",
            "--api-sock", new_vm.socket_path,
            "--config-file", config_path
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        time.sleep(1)
        if firecracker_process.poll() is not None:
            error_output = firecracker_process.stderr.read().decode('utf-8')
            print(f"[DEBUG] Erreur Firecracker : {error_output}")
            raise Exception(f"Firecracker startup failed: {error_output}")

        

        print("[DEBUG] Configuration terminée")
        
        print("[DEUG] Démarrage de Firecracker")
        
        # firecracker_url = f"http://127.0.0.1:{new_vm.port}"
        # subprocess.run(["sudo", "bash", "map_port.sh", str(new_vm.socket_path), str(new_vm.port)], check=True)
        # print(f"[DEBUG] Socket {new_vm.socket_path} mappé sur le port HTTP {new_vm.port}")
        # requests.put(f"{firecracker_url}/actions", json={"action_type": "InstanceStart"})
        return {"status": "success", "vm_id": new_vm.id, "port": new_vm.port, "socket_path": new_vm.socket_path}

    except Exception as e:
        print(f"[DEBUG] Erreur : {str(e)}")
        # Nettoyage en cas d'erreur
        if 'new_vm' in locals():
            db.delete(new_vm)
            db.commit()
        raise HTTPException(status_code=500, detail=str(e))




@app.delete("/delete_vm/{vm_id}")
async def delete_vm(vm_id: str, db: Session = Depends(get_db)):
    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        raise HTTPException(status_code=404, detail="VM not found")

    try:
        # Nettoyer le socket
        subprocess.run(["bash", "./cleanup.sh", vm.socket_path], check=True)

        # Supprimer de la base de données
        db.delete(vm)
        db.commit()

        return {"status": "success", "vm_id": vm_id}

    except Exception as e:
        return {"status": "error", "error": str(e)}, 500


@app.get("/get_all_vms")
async def get_all_vms(db: Session = Depends(get_db)):
    """
    Récupère toutes les VMs enregistrées dans la base de données.
    """
    vms = db.query(VM).all()
    return {"status": "success", "vms": vms}

@app.get("/get_user_vms/{user_id}")
async def get_user_vms(user_id: int, db: Session = Depends(get_db)):
    """
    Récupère toutes les VMs associées à un utilisateur donné.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               
    """
    vms = db.query(VM).filter(VM.user_id == user_id).all()
    return {"status": "success", "user_id": user_id, "vms": vms}

@app.post("/get_vm_stats/{vm_id}")
async def get_vms_stats(vm_id: int, db: Session = Depends(get_db)):
    """
    Récupère les statistiques sur les VMs.
    """
    vm = db.query(VM).filter(VM.id == vm_id).first()
    firecracker_url = f"http://127.0.0.1:{vm.port}"
    subprocess.run(["sudo", "bash", "map_port.sh", str(vm.socket_path), str(vm.port)], check=True)
    print(f"[DEBUG] Socket {vm.socket_path} mappé sur le port HTTP {vm.port}")
    requests.put(f"{firecracker_url}/actions", json={"action_type": "FlushMetrics"})
    return {"status": "success", "vm_id": vm_id}