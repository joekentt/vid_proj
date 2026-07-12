# Worker GPU (Google Colab)

O worker roda no Colab (T4 grátis) e conversa com a API **apenas via Redis**
(Upstash, TLS). A credencial nunca aparece no notebook: ela vive nos
**Secrets do Colab**.

**O worker é o notebook [`colab_worker.ipynb`](colab_worker.ipynb)** — abra no
Colab (`File → Open notebook → GitHub`), selecione ambiente T4, cadastre os
secrets abaixo e rode as células em ordem. Ele clona este repositório e importa
`app.models` / `app.queue` / `app.storage` da Fase 0, então o contrato
Pydantic e a máquina de estados são exatamente os da API. Cobre
**text-to-video e image-to-video** (Fase 2): cenas com
`init_image_asset_id` usam o `LTXImageToVideoPipeline`, que compartilha os
pesos com o text-to-video — nada extra de download ou VRAM. Além do
`REDIS_URL`, cadastre também `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`,
`S3_SECRET_KEY` e `S3_BUCKET` nos Secrets (o MinIO do docker-compose não é
alcançável do Colab — use Cloudflare R2 ou um MinIO exposto).

## 1. Guardar a REDIS_URL nos Secrets do Colab

1. No notebook, clique no ícone de **chave (🔑)** na barra lateral esquerda
   ("Secrets" / "Segredos").
2. **+ Adicionar novo secret**:
   - Nome: `REDIS_URL`
   - Valor: `rediss://default:SUA_SENHA@nome-do-db-12345.upstash.io:6379`
     (copiada do console do Upstash — botão de copiar em *Connect → redis-py*)
3. Ligue a chavinha **"Acesso do notebook"** para este notebook.

Os Secrets ficam gravados na sua conta Google, fora do arquivo `.ipynb` —
você pode compartilhar o notebook sem vazar a senha, e cada pessoa que rodar
precisa cadastrar o próprio secret.

## 2. Ler o secret na primeira célula

```python
import os
from google.colab import userdata

# Única linha que toca na credencial. Nada de colar a URL no código!
os.environ["REDIS_URL"] = userdata.get("REDIS_URL")
```

A partir daí todo o código do projeto (que lê `REDIS_URL` do ambiente via
`app/config.py`) funciona sem alteração.

## 3. Testar a conexão

```python
import redis

r = redis.from_url(os.environ["REDIS_URL"], decode_responses=True,
                   socket_connect_timeout=10)
print(r.ping())  # True = Colab alcançou o Upstash via TLS
```

## Cuidados no plano grátis do Upstash (500K comandos/mês)

- O `claim()` da fila usa `BRPOP` com timeout de **30s** — cada chamada conta
  como 1 comando mesmo quando não há job. Não reduza esse timeout no loop do
  worker; 30s ≈ 3K comandos/dia de polling ocioso, folgado dentro da cota.
- Atualize o progresso do job com moderação (ex.: 1 `SET` por cena concluída,
  não por frame).
- Vídeos **nunca** passam pelo Redis — só IDs e estado JSON pequeno. O binário
  sobe direto para o storage S3 (MinIO/R2), então os 256 MB e os 10 GB de
  banda do plano grátis não são gargalo.
