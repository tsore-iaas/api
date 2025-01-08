#!/bin/bash

# Activation du mode strict pour bash
set -euo pipefail

# Récupération de l'utilisateur qui a lancé sudo
REAL_USER="${SUDO_USER:-$USER}"
REAL_USER_ID=$(id -u "$REAL_USER")
REAL_GROUP_ID=$(id -g "$REAL_USER")

# Fonction de nettoyage
cleanup() {
    local work_dir="$1"
    if [ -d "$work_dir" ]; then
        echo "Nettoyage..."
        rm -rf "$work_dir"
    fi
}

# Vérification des arguments
if [ "$#" -ne 7 ]; then
    echo "Usage: $0 <squashfs_input> <ext4_output> <disk_size> <ip_address> <gateway> <hostname> <ssh_key>"
    echo "Example: $0 ubuntu-24.04.squashfs.upstream ubuntu-24.04.ext4 400M 192.168.8.200/24 192.168.8.1 my-ubuntu 'ssh-rsa AAAA...'"
    exit 1
fi

# Assignation des arguments à des variables
SQUASHFS_INPUT="$1"
EXT4_OUTPUT="$2"
DISK_SIZE="$3"
IP_ADDRESS="$4"
GATEWAY="$5"
HOSTNAME="$6"
SSH_KEY="$7"

# Vérification du fichier squashfs
if [ ! -f "$SQUASHFS_INPUT" ]; then
    echo "Erreur: Le fichier squashfs $SQUASHFS_INPUT n'existe pas"
    exit 1
fi

# Vérification que le script est exécuté en tant que root
if [ "$EUID" -ne 0 ]; then
    echo "Ce script doit être exécuté en tant que root"
    exit 1
fi

# Création du répertoire de travail
WORK_DIR=$(mktemp -d)
echo "Utilisation du répertoire temporaire: $WORK_DIR"

# Configuration du trap pour le nettoyage
trap 'cleanup "$WORK_DIR"' EXIT

# Extraction du squashfs
echo "Extraction du squashfs..."
unsquashfs -d "$WORK_DIR/squashfs-root" "$SQUASHFS_INPUT"

# Configuration SSH
echo "Configuration des clés SSH..."
mkdir -p "$WORK_DIR/squashfs-root/root/.ssh"
echo "$SSH_KEY" > "$WORK_DIR/squashfs-root/root/.ssh/authorized_keys"
chmod 700 "$WORK_DIR/squashfs-root/root/.ssh"
chmod 600 "$WORK_DIR/squashfs-root/root/.ssh/authorized_keys"

# Création du script rc.local pour la configuration réseau au démarrage
echo "Configuration du script de démarrage réseau..."
cat > "$WORK_DIR/squashfs-root/etc/rc.local" << EOF
#!/bin/bash
# Configuration réseau
ip addr add $IP_ADDRESS dev eth0
ip link set eth0 up
ip route add default via $GATEWAY

exit 0
EOF

# Rendre rc.local exécutable
chmod +x "$WORK_DIR/squashfs-root/etc/rc.local"

# Activer le service rc-local
ln -sf /lib/systemd/system/rc-local.service "$WORK_DIR/squashfs-root/etc/systemd/system/rc-local.service"

# Configuration DNS
echo "Configuration DNS..."
cat > "$WORK_DIR/squashfs-root/etc/resolv.conf" << EOF
nameserver 8.8.8.8
nameserver $GATEWAY
EOF

# Configuration hostname
echo "Configuration du hostname..."
echo "$HOSTNAME" > "$WORK_DIR/squashfs-root/etc/hostname"
cat > "$WORK_DIR/squashfs-root/etc/hosts" << EOF
127.0.0.1   localhost
127.0.0.1   $HOSTNAME
EOF

# Création de l'image ext4
echo "Création de l'image ext4..."
truncate -s "$DISK_SIZE" "$EXT4_OUTPUT"
mkfs.ext4 -d "$WORK_DIR/squashfs-root" -F "$EXT4_OUTPUT"
echo "Debug: Output file is $EXT4_OUTPUT" >> debug.log
# Changement des propriétaires de l'image
echo "Attribution des droits à l'utilisateur $REAL_USER..."
chown "$REAL_USER_ID":"$REAL_GROUP_ID" "$EXT4_OUTPUT"
chmod 644 "$EXT4_OUTPUT"

echo "Terminé ! L'image ext4 a été créée avec succès: $EXT4_OUTPUT"
echo "Les permissions ont été données à l'utilisateur $REAL_USER"