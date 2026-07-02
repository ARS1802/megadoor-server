# Servidor de Arquivos HTTPS com FastAPI

Este projeto cria uma API simples para compartilhar arquivos em uma rede local.

Fluxo principal:

1. Um computador cliente faz uma requisicao HTTPS informando um caminho.
2. Este servidor procura esse caminho dentro da pasta compartilhada.
3. O servidor responde com a lista, com um arquivo, com um `.zip` de um
   diretorio, ou salva arquivos enviados pelo cliente.

Por seguranca, os clientes nao podem pedir caminhos livres do sistema, como
`/etc/passwd`. Eles sempre pedem caminhos relativos dentro de uma pasta base.
Todos os endpoints exigem um token no header `Authorization`.
O servidor tambem libera CORS para permitir que um HTML aberto em outro
computador da rede use `fetch()` no navegador.

## Estrutura

```text
.
├── app/
│   └── main.py
├── START_SERVER
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

O jeito mais simples e usar o script `START_SERVER`:

```bash
./START_SERVER
```

Para compartilhar uma pasta especifica:

```bash
./START_SERVER "$HOME/scripts"
```

Ao iniciar, o script gera 16 tokens novos com `secrets.token_urlsafe(16)`.
Esses tokens ficam fixos durante essa execucao do servidor, aparecem no terminal
e tambem sao escritos em um arquivo chamado `tokenList` dentro da pasta
compartilhada.

Cada requisicao precisa enviar um desses tokens:

```text
Authorization: Bearer TOKEN
```

O `--host 0.0.0.0` permite que outros dispositivos da rede acessem a API.
O CORS ja esta habilitado no FastAPI para uso em rede local. Em termos simples,
isso permite que uma pagina HTML aberta em outro computador faca requisicoes ao
servidor usando JavaScript e o header `Authorization`.

Se o firewall estiver ativo, libere a porta:

```bash
sudo ufw allow 8443/tcp
```

## 6. Testar a partir de outro computador da rede

Escolha um token impresso pelo `START_SERVER` e salve em uma variavel:

```bash
TOKEN="cole-um-token-aqui"
```

Verificar se a API esta viva:

```bash
curl -k -H "Authorization: Bearer $TOKEN" https://192.168.1.50:8443/health
```

Listar a pasta compartilhada:

```bash
curl -k -H "Authorization: Bearer $TOKEN" -X POST -F "path=." "https://192.168.1.50:8443/api/list"
```

Listar um subdiretorio:

```bash
curl -k -H "Authorization: Bearer $TOKEN" -X POST -F "path=documentos" "https://192.168.1.50:8443/api/list"
```

Baixar um arquivo:

```bash
curl -k -H "Authorization: Bearer $TOKEN" -OJ -X POST -F "path=documentos/nota.txt" "https://192.168.1.50:8443/api/download"
```

Baixar um diretorio como `.zip`:

```bash
curl -k -H "Authorization: Bearer $TOKEN" -OJ -X POST -F "path=documentos" "https://192.168.1.50:8443/api/archive"
```

Criar uma pasta:

```bash
curl -k -H "Authorization: Bearer $TOKEN" -X POST -F "path=documentos/projetos" "https://192.168.1.50:8443/api/folders"
```

Enviar um arquivo:

```bash
curl -k -H "Authorization: Bearer $TOKEN" -X POST \
  -F "path=documentos" \
  -F "file=@/home/seu-usuario/nota.txt" \
  "https://192.168.1.50:8443/api/upload"
```

Deletar um arquivo ou pasta:

```bash
curl -k -H "Authorization: Bearer $TOKEN" -X POST \
  -F "path=documentos/nota.txt" \
  "https://192.168.1.50:8443/api/delete"
```

Mover um arquivo ou pasta:

```bash
curl -k -H "Authorization: Bearer $TOKEN" -X POST \
  -F "source_path=documentos/nota.txt" \
  -F "target_path=backup/nota.txt" \
  "https://192.168.1.50:8443/api/move"
```

Copiar um arquivo ou pasta:

```bash
curl -k -H "Authorization: Bearer $TOKEN" -X POST \
  -F "source_path=documentos" \
  -F "target_path=copia-documentos" \
  "https://192.168.1.50:8443/api/copy"
```

Renomear um arquivo ou pasta:

```bash
curl -k -H "Authorization: Bearer $TOKEN" -X POST \
  -F "source_path=documentos/nota.txt" \
  -F "new_name=nota-final.txt" \
  "https://192.168.1.50:8443/api/rename"
```

Por padrao, `move`, `copy` e `rename` recusam sobrescrever destinos que ja
existem. Para permitir substituicao, envie tambem:

```bash
-F "overwrite=true"
```

## Endpoints

| Metodo | Caminho | O que faz |
| --- | --- | --- |
| `GET` | `/health` | Mostra se a API esta funcionando |
| `POST` | `/api/list` | Lista arquivos e diretorios usando `FormData` com `path` |
| `POST` | `/api/download` | Baixa um arquivo usando `FormData` com `path` |
| `POST` | `/api/archive` | Baixa um diretorio como `.zip` usando `FormData` com `path` |
| `POST` | `/api/folders` | Cria uma pasta usando `FormData` com `path` |
| `POST` | `/api/upload` | Envia um arquivo usando `FormData` com `path` e `file` |
| `POST` | `/api/delete` | Deleta arquivo ou pasta usando `FormData` com `path` |
| `POST` | `/api/move` | Move arquivo ou pasta usando `source_path`, `target_path` e `overwrite` |
| `POST` | `/api/copy` | Copia arquivo ou pasta usando `source_path`, `target_path` e `overwrite` |
| `POST` | `/api/rename` | Renomeia arquivo ou pasta usando `source_path`, `new_name` e `overwrite` |

No JavaScript, o padrao fica assim:

```js
const formData = new FormData();
formData.append("path", "documentos");

await fetch("https://192.168.1.50:8443/api/list", {
  method: "POST",
  headers: {
    Authorization: `Bearer ${TOKEN}`,
  },
  body: formData,
});
```

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
- Os tokens mudam sempre que `START_SERVER` e executado novamente.
- Quem tiver um token valido consegue acessar os arquivos pela API.
- O arquivo `tokenList` contem segredos. Nao envie esse arquivo para GitHub,
  chat, email ou outros lugares publicos.
- Para uso real, adicione usuarios, permissoes por pasta e logs mais completos.
