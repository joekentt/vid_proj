# AI Video Studio

Sistema de geração de vídeo por IA (text-to-video + image-to-video) com
encadeamento de cenas, pipeline de áudio e frontend web.

Arquitetura desacoplada: a geração pesada roda num **worker GPU** (Google Colab
grátis, depois RunPod), enquanto frontend e API rodam em qualquer lugar leve.
A máquina do usuário final só precisa de um navegador.

## Arquitetura

```
Frontend (Next.js)  ──HTTP──>  API (FastAPI)  ──>  Redis (fila)
                                                       │
                                                       ▼
Storage (R2/MinIO)  <───────  Worker GPU (Colab) ──pega job, gera, sobe
```

O **Redis** é o ponto de encontro: API e worker nunca falam direto. Isso permite
trocar o worker (Colab → RunPod) sem mexer no resto.

## Estrutura

| Pasta       | Papel                                              |
|-------------|----------------------------------------------------|
| `backend/`  | API FastAPI + orquestração + fila + storage        |
| `worker/`   | Roda no Colab: pega jobs, gera vídeo, faz upload    |
| `frontend/` | Next.js: editor de cenas, galeria                  |

## Como rodar (Fase 0)

Sobe Redis + MinIO + API localmente:

```bash
docker compose up
```

- API:            http://localhost:8000
- Docs (Swagger): http://localhost:8000/docs
- MinIO console:  http://localhost:9001  (minioadmin / minioadmin)

### Teste rápido

```bash
# cria um job com 2 cenas
curl -X POST http://localhost:8000/jobs \
  -H 'Content-Type: application/json' \
  -d '{
    "title": "Meu primeiro vídeo",
    "scenes": [
      {"id": "s0", "order": 0, "prompt": "Drone sobrevoa montanhas ao amanhecer"},
      {"id": "s1", "order": 1, "prompt": "A câmera desce até um lago espelhado"}
    ]
  }'

# consulta o estado (use o id retornado acima)
curl http://localhost:8000/jobs/SEU_JOB_ID
```

O job fica em `pending` até o worker conectar (Fase 1).

## Configuração

Tudo via `.env` (copie `backend/.env.example` para `backend/.env`; veja
`backend/app/config.py` para todos os campos). Defaults apontam para o
docker-compose local, então **nenhuma chave externa é necessária** para
começar. Para usar Cloudflare R2 em vez de MinIO, troque `S3_ENDPOINT_URL`
e as credenciais.

O `.env` real contém credenciais e está no `.gitignore` — nunca commite.

## Redis público (Upstash) — para o worker no Colab

O worker roda fora da sua rede (Colab), então precisa de um Redis alcançável
pela internet. Usamos o **Upstash** (plano grátis: 256 MB, 500K comandos/mês,
TLS sempre ligado). O plano grátis do Redis Cloud foi descartado por **não
oferecer TLS**.

1. Crie conta em <https://console.upstash.com> (login com GitHub/Google).
2. **Create Database** → tipo *Regional*, região **`us-east-1`** (mais próxima
   dos GPUs do Colab), *Eviction* **desligado** (fila não pode perder chave).
3. Na aba **Connect** do banco, copie a URL do bloco *redis-py* — formato
   `rediss://default:SENHA@nome-12345.upstash.io:6379` (o `rediss://` com
   dois "s" é o TLS).
4. Onde colar:
   - **API**: `REDIS_URL=` no `backend/.env` (ou variável de ambiente do
     host onde a API roda).
   - **Worker no Colab**: nos **Secrets do Colab**, nunca no notebook —
     passo a passo em [`worker/README.md`](worker/README.md).

Trocar entre local e Upstash é só trocar a `REDIS_URL`; com `docker compose up`
o compose já injeta `redis://redis:6379/0` e nada muda.

## Roadmap

- [x] **Fase 0** — Esqueleto, contratos, fila, storage
- [ ] **Fase 1** — MVP vertical: 1 clipe text-to-video ponta a ponta (worker Colab + LTX-Video)
- [ ] **Fase 2** — Image-to-video + parâmetros avançados
- [ ] **Fase 3** — Scene chaining (concatenação FFmpeg, consistência de personagem)
- [ ] **Fase 4** — Áudio: TTS, lip-sync, música, mixagem
- [ ] **Fase 5** — Pós-processamento: upscaling, interpolação, export
- [ ] **Fase 6** — Editor de timeline, galeria, auth, créditos
