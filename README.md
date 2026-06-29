# Servidor de Arquivos HTTPS com FastAPI

Este projeto cria uma API simples para compartilhar arquivos em uma rede local.

Fluxo principal:

1. Um computador cliente faz uma requisicao HTTPS informando um caminho.
2. Este servidor procura esse caminho dentro da pasta compartilhada.
3. O servidor responde com a lista, com um arquivo, ou com um `.zip` de um diretorio.

Por seguranca, os clientes nao podem pedir caminhos livres do sistema, como
`/etc/passwd`. Eles sempre pedem caminhos relativos dentro de uma pasta base.

## Estrutura

```text
.
├── app/
│   └── main.py
├── scripts/
│   └── create-cert.sh
├── shared_files/
│   └── README.txt
├── tests/
│   └── test_app.py
├── requirements.txt
└── README.md
```

## 1. Preparar o ambiente Python

No Ubuntu Server, dentro da pasta do projeto:

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip openssl
```

Depois crie o ambiente virtual do projeto:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 2. Escolher a pasta compartilhada

Por padrao, o servidor compartilha a pasta `shared_files/`.

Para usar outra pasta, defina `FILE_SERVER_ROOT` antes de iniciar o servidor:

```bash
export FILE_SERVER_ROOT=/home/seu-usuario/arquivos-compartilhados
```

Crie a pasta, se necessario:

```bash
mkdir -p "$FILE_SERVER_ROOT"
```

## 3. Descobrir o IP deste servidor

Como este computador esta conectado via Ethernet ao roteador, descubra o IP local:

```bash
hostname -I
```

Exemplo de resultado:

```text
192.168.1.50
```

Use o IP real do seu servidor nos proximos comandos.

## 4. Gerar certificado HTTPS local

Para aprendizado em rede local, este projeto usa um certificado autoassinado.
Os clientes vao precisar aceitar o certificado ou usar `curl -k`.

```bash
chmod +x scripts/create-cert.sh
./scripts/create-cert.sh 192.168.1.50
```

Troque `192.168.1.50` pelo IP real do servidor.

## 5. Iniciar o servidor

```bash
uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 8443 \
  --ssl-keyfile certs/server.key \
  --ssl-certfile certs/server.crt
```

O `--host 0.0.0.0` permite que outros dispositivos da rede acessem a API.

Se o firewall estiver ativo, libere a porta:

```bash
sudo ufw allow 8443/tcp
```

## 6. Testar a partir de outro computador da rede

Verificar se a API esta viva:

```bash
curl -k https://192.168.1.50:8443/health
```

Listar a pasta compartilhada:

```bash
curl -k "https://192.168.1.50:8443/api/list?path=."
```

Listar um subdiretorio:

```bash
curl -k "https://192.168.1.50:8443/api/list?path=documentos"
```

Baixar um arquivo:

```bash
curl -k -OJ "https://192.168.1.50:8443/api/download?path=documentos/nota.txt"
```

Baixar um diretorio como `.zip`:

```bash
curl -k -OJ "https://192.168.1.50:8443/api/archive?path=documentos"
```

## Endpoints

| Metodo | Caminho | O que faz |
| --- | --- | --- |
| `GET` | `/health` | Mostra se a API esta funcionando |
| `GET` | `/api/list?path=.` | Lista arquivos e diretorios |
| `GET` | `/api/download?path=arquivo.txt` | Baixa um arquivo |
| `GET` | `/api/archive?path=pasta` | Baixa um diretorio como `.zip` |

## Documentacao interativa

Com o servidor rodando, abra no navegador:

```text
https://192.168.1.50:8443/docs
```

O navegador deve mostrar um aviso porque o certificado e autoassinado.
Para estudo em rede local, isso e esperado.

## Rodar testes

```bash
pytest
```

## Observacoes de seguranca

- Nao exponha este servidor diretamente na internet.
- Use uma pasta compartilhada especifica, nao a raiz do sistema.
- Este projeto nao tem login/senha. Qualquer dispositivo da sua rede que consiga
  acessar a porta `8443` podera pedir arquivos da pasta compartilhada.
- Para uso real, adicione autenticacao, controle de permissao e logs mais completos.
