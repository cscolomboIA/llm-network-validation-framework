#!/bin/bash
# =============================================================================
# NetValidAI — Deploy na AWS EC2
# Execute este script na sua máquina local com AWS CLI configurado.
# Pré-requisito: aws configure (Access Key + Secret + Region)
# =============================================================================

set -e

# ---------------------------------------------------------------------------
# CONFIGURAÇÕES — edite conforme necessário
# ---------------------------------------------------------------------------
REGION="us-east-1"          # Altere para sua região preferida
INSTANCE_TYPE_API="t3.medium"     # Backend + RAG + Healing
INSTANCE_TYPE_MN="t3.large"       # Mininet (precisa de mais CPU)
AMI_ID="ami-0c02fb55956c7d316"    # Amazon Linux 2023 us-east-1 (atualize se mudar região)
KEY_NAME="netvalidai-key"          # Nome do par de chaves EC2
PROJECT_NAME="netvalidai"
SG_NAME="netvalidai-sg"

echo "=== NetValidAI Deploy AWS ==="
echo "Região: $REGION | API: $INSTANCE_TYPE_API | Mininet: $INSTANCE_TYPE_MN"

# ---------------------------------------------------------------------------
# 1. Criar par de chaves SSH (se não existir)
# ---------------------------------------------------------------------------
if ! aws ec2 describe-key-pairs --key-names "$KEY_NAME" --region "$REGION" &>/dev/null; then
    echo "[1/7] Criando par de chaves SSH..."
    aws ec2 create-key-pair \
        --key-name "$KEY_NAME" \
        --region "$REGION" \
        --query "KeyMaterial" \
        --output text > "${KEY_NAME}.pem"
    chmod 400 "${KEY_NAME}.pem"
    echo "      Chave salva em ${KEY_NAME}.pem — GUARDE EM LOCAL SEGURO"
else
    echo "[1/7] Par de chaves '$KEY_NAME' já existe"
fi

# ---------------------------------------------------------------------------
# 2. Criar Security Group
# ---------------------------------------------------------------------------
echo "[2/7] Configurando Security Group..."
SG_ID=$(aws ec2 create-security-group \
    --group-name "$SG_NAME" \
    --description "NetValidAI security group" \
    --region "$REGION" \
    --query "GroupId" --output text 2>/dev/null || \
    aws ec2 describe-security-groups \
        --group-names "$SG_NAME" \
        --region "$REGION" \
        --query "SecurityGroups[0].GroupId" --output text)

echo "      Security Group: $SG_ID"

# Regras de entrada
for PORT in 22 80 443 8000 8001 8002 8003 8004; do
    aws ec2 authorize-security-group-ingress \
        --group-id "$SG_ID" \
        --protocol tcp \
        --port "$PORT" \
        --cidr "0.0.0.0/0" \
        --region "$REGION" 2>/dev/null || true
done

# ---------------------------------------------------------------------------
# 3. User data — instalação automática na instância API
# ---------------------------------------------------------------------------
API_USERDATA=$(cat <<'EOF'
#!/bin/bash
yum update -y
yum install -y docker git
systemctl start docker
systemctl enable docker
usermod -aG docker ec2-user

# Docker Compose v2
mkdir -p /usr/local/lib/docker/cli-plugins
curl -SL https://github.com/docker/compose/releases/download/v2.24.0/docker-compose-linux-x86_64 \
    -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

# Clona o repositório (ajuste para seu fork)
cd /home/ec2-user
git clone https://github.com/cscolomboIA/llm-network-validation-framework.git netvalidai
chown -R ec2-user:ec2-user netvalidai

echo "NetValidAI: instância API pronta. Configure .env e execute: docker compose up -d"
EOF
)

# ---------------------------------------------------------------------------
# 4. User data — instalação para instância Mininet
# ---------------------------------------------------------------------------
MN_USERDATA=$(cat <<'EOF'
#!/bin/bash
yum update -y
yum install -y docker git python3 python3-pip

# Dependências do Mininet
pip3 install mininet 2>/dev/null || true
yum install -y openvswitch 2>/dev/null || apt-get install -y openvswitch-switch 2>/dev/null || true
systemctl start openvswitch 2>/dev/null || true

systemctl start docker
systemctl enable docker
usermod -aG docker ec2-user

mkdir -p /usr/local/lib/docker/cli-plugins
curl -SL https://github.com/docker/compose/releases/download/v2.24.0/docker-compose-linux-x86_64 \
    -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

cd /home/ec2-user
git clone https://github.com/cscolomboIA/llm-network-validation-framework.git netvalidai
chown -R ec2-user:ec2-user netvalidai

echo "NetValidAI: instância Mininet pronta."
EOF
)

# ---------------------------------------------------------------------------
# 5. Lançar instâncias EC2
# ---------------------------------------------------------------------------
echo "[3/7] Lançando instância API (backend)..."
API_INSTANCE=$(aws ec2 run-instances \
    --image-id "$AMI_ID" \
    --instance-type "$INSTANCE_TYPE_API" \
    --key-name "$KEY_NAME" \
    --security-group-ids "$SG_ID" \
    --region "$REGION" \
    --user-data "$API_USERDATA" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=${PROJECT_NAME}-api},{Key=Project,Value=${PROJECT_NAME}}]" \
    --query "Instances[0].InstanceId" --output text)

echo "      API Instance ID: $API_INSTANCE"

echo "[4/7] Lançando instância Mininet (privileged)..."
MN_INSTANCE=$(aws ec2 run-instances \
    --image-id "$AMI_ID" \
    --instance-type "$INSTANCE_TYPE_MN" \
    --key-name "$KEY_NAME" \
    --security-group-ids "$SG_ID" \
    --region "$REGION" \
    --user-data "$MN_USERDATA" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=${PROJECT_NAME}-mininet},{Key=Project,Value=${PROJECT_NAME}}]" \
    --query "Instances[0].InstanceId" --output text)

echo "      Mininet Instance ID: $MN_INSTANCE"

# ---------------------------------------------------------------------------
# 6. Aguardar IPs públicos
# ---------------------------------------------------------------------------
echo "[5/7] Aguardando instâncias iniciarem..."
aws ec2 wait instance-running --instance-ids "$API_INSTANCE" "$MN_INSTANCE" --region "$REGION"

API_IP=$(aws ec2 describe-instances \
    --instance-ids "$API_INSTANCE" \
    --region "$REGION" \
    --query "Reservations[0].Instances[0].PublicIpAddress" --output text)

MN_IP=$(aws ec2 describe-instances \
    --instance-ids "$MN_INSTANCE" \
    --region "$REGION" \
    --query "Reservations[0].Instances[0].PublicIpAddress" --output text)

echo "      API IP:     $API_IP"
echo "      Mininet IP: $MN_IP"

# ---------------------------------------------------------------------------
# 7. Salvar configuração para próximos passos
# ---------------------------------------------------------------------------
echo "[6/7] Salvando configuração..."
cat > .aws-deploy.env <<ENVEOF
API_INSTANCE=$API_INSTANCE
MN_INSTANCE=$MN_INSTANCE
API_IP=$API_IP
MN_IP=$MN_IP
REGION=$REGION
KEY_NAME=$KEY_NAME
SG_ID=$SG_ID
ENVEOF

echo "[7/7] Deploy concluído!"
echo ""
echo "============================================================"
echo "  PRÓXIMOS PASSOS:"
echo "============================================================"
echo ""
echo "1. Configure o .env na instância API:"
echo "   ssh -i ${KEY_NAME}.pem ec2-user@${API_IP}"
echo "   cd netvalidai && cp .env.example .env && nano .env"
echo ""
echo "2. Adicione o IP do Mininet no .env:"
echo "   MININET_HOST=${MN_IP}"
echo ""
echo "3. Inicie o backend:"
echo "   docker compose up -d"
echo ""
echo "4. Instale o Mininet na instância dedicada:"
echo "   ssh -i ${KEY_NAME}.pem ec2-user@${MN_IP}"
echo "   sudo mn --version    # testa instalação"
echo ""
echo "5. Acesse a plataforma:"
echo "   http://${API_IP}:8000/docs   (API Swagger)"
echo "   ws://${API_IP}:8000/ws/{session-id}  (WebSocket)"
echo ""
echo "ESTIMATIVA DE CUSTO AWS:"
echo "  t3.medium (API):    ~\$0.04/hora = ~\$30/mês"
echo "  t3.large (Mininet): ~\$0.08/hora = ~\$60/mês"
echo "  Total estimado:     ~\$90/mês (pare as instâncias quando não usar!)"
echo ""
echo "Para parar: aws ec2 stop-instances --instance-ids $API_INSTANCE $MN_INSTANCE --region $REGION"
