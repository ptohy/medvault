# MedVault HealthOps — V6.2.2

Sistema self-hosted para receitas médicas, pedidos de exame, resultados, tratamentos, agenda e integrações locais.

## Estrutura correta do GitHub

A raiz do repositório precisa ficar exatamente assim:

```text
backend/
frontend/
docker-compose.yml
portainer-stack.yml
README.md
.gitignore
CHECK_STRUCTURE.txt
```

## Portainer

Use o repositório GitHub e configure:

```text
Compose path: portainer-stack.yml
```

Não use caminho para ZIP, README, Dockerfile ou arquivo interno.

## Acesso

```text
Frontend: http://192.168.50.201:8090
Backend:  http://192.168.50.201:8088
```

## Configuração padrão

```text
OLLAMA_BASE_URL=http://192.168.50.112:11434
OLLAMA_MODEL=qwen2.5:7b
VITE_API_BASE=http://192.168.50.201:8088
```

As integrações também podem ser ajustadas depois pela interface:

```text
Configurações > Integrações
```

## Validação

O arquivo `portainer-stack.yml` precisa começar com:

```yaml
services:
  medvault-backend:
```

Se começar com `import React`, `@tailwind`, `FROM node` ou qualquer outra coisa, o upload no GitHub foi feito errado.


## V6.2.3

Correção:
- Corrige `Error binding parameter 4: type 'dict' is not supported`.
- Valores vindos da IA/OCR/QRCode são convertidos para texto/JSON antes de salvar no SQLite.


## V6.3.0

Correções e melhorias implementadas:
- Remove IP hardcoded dos callbacks: usa `SELF_BASE_URL`.
- Adiciona configuração `self_base_url` na interface.
- APScheduler executa side-effects a cada 5 minutos; `/api/status` e `/api/bootstrap` não disparam mais webhooks/ICS.
- Limpeza de `share_tokens` expirados e `ingest_jobs` antigos.
- Limpeza de `health_fts` ao excluir documento fonte.
- Deduplicação básica por SHA-256 do arquivo importado.
- Endpoint de reprocessamento OCR/IA: `POST /api/source-documents/{id}/reprocess`.
- Botão `Reprocessar OCR/IA` na aba Documentos.
- `classify_page` menos frágil, com scoring por termos em vez de match único.


## V6.3.2

Correção crítica:
- Corrige `NameError: name 'scheduler' is not defined` no startup do backend.
- Backend deixa de reiniciar em loop.


## V6.3.3

Correção crítica:
- Insere `scheduler = None` de fato no escopo global após criação do FastAPI.
- Corrige falso positivo da V6.3.2, onde o pacote podia conter `if scheduler is None`, mas não a variável global.


## V6.3.4

Correção crítica:
- Corrige definitivamente `Error binding parameter 4: type 'dict' is not supported`.
- O endpoint síncrono `/api/ingest/upload` agora calcula `file_hash` antes do INSERT.
- `file_sha256` recebe `safe_db_text(file_hash)`, não string vazia inconsistente.
- `source_url`, `qr_text`, `message`, `stage`, logs e campos vindos da IA/OCR são normalizados antes de gravar no SQLite.


## V6.3.5

Correções aplicadas após auditoria:
- `title`, `doctor` e `crm` vindos do Ollama agora passam por `safe_db_text`.
- Adicionado `safe_ai_field()` para normalizar campos retornados como objeto/lista pelo modelo.
- Corrigido `file_hash` no endpoint síncrono `/api/ingest/upload`.
- `file_hash` agora é inicializado/calculado antes do INSERT ou cai em fallback seguro.
- Callbacks de tratamento usam `current_self_base_url()`.
- `/api/settings` retorna `api_base` e `n8n_endpoint` a partir de `SELF_BASE_URL`.
- Mantidas correções de APScheduler, FTS cleanup, cleanup de jobs/tokens e deduplicação.


## V6.3.6

Correção de débito técnico:
- Remove `locals().get('file_hash', '')`.
- Calcula `file_hash = sha256_file(UPLOAD_DIR / file_name)` no endpoint síncrono `/api/ingest/upload`.
- Ativa deduplicação real também no upload direto pelo frontend.


## V6.3.7

Correções:
- Adicionado `apscheduler==3.10.4` ao `requirements.txt`.
- Endurecimento de download de uploads por nome usando `Path(name).name`.
- Busca FTS passa a escapar aspas e tratar consulta como frase literal.
- `classify_page` agora exige score mínimo 2 e evita empate, caindo em `document`.
- Reprocessamento limpa derivados e entradas antigas do `health_fts` antes de reprocessar.
- Frontend informa quando o upload foi ignorado por duplicidade.


## V6.3.8

Adicionado:
- Botão `Resetar tudo` em `Configurações > Sistema`.
- Endpoint `POST /api/system/reset`.
- Exige confirmação textual `RESETAR`.
- Apaga banco SQLite, uploads e exports, recriando a base limpa.


## V6.3.9

Correções críticas:
- Define `EXPORT_DIR=/exports` e cria o diretório no startup.
- Adiciona volume `medvault_exports:/exports`.
- Corrige reset da base: remove SQLite, WAL/SHM, uploads e exports e recria schema.
- Pausa o APScheduler durante o reset para evitar concorrência.
- Adiciona recuperação automática de schema (`ensure_core_schema`) em `/api/status`, `/api/bootstrap` e upload.
- Corrige erros após reset parcial:
  - `NameError: EXPORT_DIR is not defined`
  - `sqlite3.OperationalError: no such table: ingest_jobs`
  - `sqlite3.OperationalError: no such table: treatment_events`


## V6.3.10

Correção de deploy:
- `portainer-stack.yml` e `docker-compose.yml` regravados do zero.
- Corrige volume `medvault_exports` referenciado mas não declarado.
- Volumes finais:
  - `medvault_data`
  - `medvault_uploads`
  - `medvault_exports`


## V6.3.11

Correção de frontend/API:
- Frontend passa a usar API relativa por padrão (`/api`), sem depender de IP/porta no bundle.
- Nginx do frontend agora faz proxy:
  - `/api/*` → `medvault-backend:8088/api/*`
  - `/uploads/*` → `medvault-backend:8088/uploads/*`
  - `/health` → `medvault-backend:8088/health`
- Corrige erro HTML `404 Not Found nginx` quando o frontend tentava chamar rota API no próprio Nginx sem proxy.


## V6.3.12

Correção crítica do reset:
- Reset não tenta mais apagar o SQLite enquanto o processo mantém conexões abertas.
- O reset agora dropa todos os objetos do SQLite (`tables`, `views`, `indexes`, `triggers`).
- Limpa uploads e exports antes de recriar a base.
- Remove WAL/SHM remanescentes.
- Recria o schema limpo com `init_db()`.
- Pausa e reinicia o APScheduler durante o reset.
- Após reset, receitas, tratamentos, exames, eventos e jobs antigos devem desaparecer de fato.


## V6.3.13

Correção de calendário:
- Botão de calendário agora envia a URL ICS digitada antes de sincronizar.
- `/api/settings/test/calendar` aceita `calendar_ics_url` no payload e salva antes do teste.
- `/api/calendar/sync` também aceita `calendar_ics_url`.
- Interface mostra mensagem clara: `Calendário sincronizado. Eventos importados: X`.

## V6.5.0

Adicionado:
- Estoque de medicamentos/vitaminas.
- Cadastro de itens comprados com quantidade, unidade, alerta mínimo e valor de compra.
- Histórico de compras e preço unitário por item.
- Controle de estoque para medicamentos com e sem receita.
- Consumo automático de estoque ao marcar evento como tomado/aplicado.
- Notificação de estoque baixo via Home Assistant.
- Suporte a lembretes de medicamentos/vitaminas sem receita, como Centrum diário.
- Mantém controle separado de receita controlada: receita pode esgotar por 3/6 aplicações, enquanto estoque físico também é controlado.
- Package Home Assistant em `home-assistant/medvault-home-assistant-package.yaml`.


## V6.5.1

Correção de deploy:
- Corrige falha no build do frontend no passo `npm ci`/`npm install`.
- Remove lockfiles gerados.
- Dockerfile do frontend não usa mais `npm ci`.
- Dockerfile usa `npm install --no-audit --no-fund --cache /tmp/npm-cache`.


## V6.5.2

Correções:
- Corrige deslocamento de data/hora no frontend usando parsing local de datas ISO.
- Remove o texto `DIA/MÊS` dos cards.
- Corrige parsing de eventos Google Calendar UTC (`Z`) convertendo para horário do Brasil.
- Replaneja eventos de tratamento após sincronizar calendário.
- Botão `Sincronizar calendário` da Agenda agora chama `/api/calendar/sync`, mostra resultado e recarrega a tela.
- Melhora a UI da aba Estoque.


## V6.5.3

Correções de UX:
- Cards de data no estilo agenda: dia da semana, dia grande, mês e horário.
- Botões de ações pendentes renomeados para explicar melhor a função.
- Após qualquer ação aparece opção `Desfazer` por 10 segundos.
- Novo endpoint `POST /api/treatment-events/{id}/undo`.
- Desfazer reverte status, estoque consumido e uso de receita quando aplicável.


## V6.5.5

Correção crítica:
- Reset agora remove fisicamente o SQLite (`medvault.sqlite3`, `-wal`, `-shm`) e recria o schema.
- Evita falhas com tabelas sombra do FTS5.
- Limpa uploads e exports.
- Retorna contadores pós-reset para confirmar que tudo zerou.
- Campo de confirmação aceita `RESETAR` com espaços acidentais removidos.
- Interface mostra erro real se o reset falhar.

## V6.5.6

Correções de calendário:
- Eventos de aplicação/injetáveis entram obrigatoriamente na sincronização do calendário.
- Corrige `CALENDAR_SYNC_KEYWORDS` antigo no compose que excluía aplicação de medicamentos.
- Normaliza acentos na filtragem de eventos ICS.
- Dashboard mostra `Próximos eventos de saúde`, incluindo consultas, exames e aplicações.
- Ordenação dos eventos reforçada no frontend.

## V6.6.0

Calendário inteligente:
- A sincronização do calendário deixou de depender apenas de lista fixa de palavras-chave.
- Eventos agora passam por classificação híbrida:
  - heurística local rápida;
  - nomes de tratamentos/estoque cadastrados;
  - IA via Ollama para eventos ambíguos.
- A IA identifica consultas, exames, aplicações, lembretes de medicamento em casa e eventos relacionados a medicamentos cadastrados.
- Eventos de farmácias diferentes, outro medicamento ou Mounjaro em casa passam a ser reconhecidos por contexto.
- Eventos não médicos como futebol, reuniões comuns e lazer continuam sendo descartados.

## V6.6.1

Correção geral de integrações:
- Corrige bug em que `Salvar e sincronizar` do calendário parecia não funcionar quando o banco vinha de versão anterior.
- Adiciona migração faltante em `calendar_events` (`classification_type`, `classification_confidence`, `classification_reason`).
- Botão de calendário agora salva os campos preenchidos e sincroniza diretamente via `/api/calendar/sync`.
- Botão `Salvar integrações` agora mostra erro real se o backend falhar.
- Testes de Ollama e Home Assistant também salvam os valores digitados antes de testar.
- Settings não apagam segredos salvos quando o campo está vazio/mascarado.
- Endpoint de upload endurecido contra path traversal.

## V6.6.3

Correção crítica:
- Refeito a partir da V6.6.1 completa; a V6.6.2 saiu com `main.py` truncado e perdeu rotas como `/api/status` e `/api/bootstrap`.
- Mantidas todas as rotas principais do backend.
- Sincronização do calendário não chama Ollama em lote por padrão, evitando 502/timeout.
- IA no calendário fica opcional:
  - `CALENDAR_AI_SYNC_ENABLED=false`
  - `CALENDAR_AI_SYNC_MAX_EVENTS=3`
- Erros HTML/proxy no frontend agora aparecem com status e rota.

## V6.6.4

Correção crítica:
- Remove chamada ao Ollama dentro da sincronização do calendário.
- Acaba com spam de `Falha na classificação IA de evento do calendário`.
- Classificação do calendário passa a ser local/contextual:
  - tratamentos cadastrados;
  - medicamentos em estoque;
  - termos de saúde;
  - aplicações/injetáveis;
  - medicamentos em casa.
- Limpa logs antigos gerados pelo bug da classificação IA.
- Compose força `CALENDAR_AI_SYNC_ENABLED=false`.

## V6.6.5

Correção crítica de frontend:
- Corrige `n.trim is not a function`.
- O helper `api()` agora trata corretamente erros JSON cujo `detail` vem como objeto/lista do FastAPI.
- Se o backend/proxy responder HTML, a mensagem mostra HTTP status e rota.
- Adicionado `/api/debug/routes` para confirmar rotas ativas do backend.

## V6.6.6

Correção crítica:
- Corrige decorator `/api/status` que foi aplicado acidentalmente ao helper `cleanup_calendar_ai_warning_spam(conn)`.
- Esse bug fazia o FastAPI exigir query parameter `conn`, gerando erro:
  `[{"type":"missing","loc":["query","conn"],"msg":"Field required"}]`.
- Adicionado `/api/self-test` para validar rotas críticas.
- `/api/debug/routes` agora mostra rota, nome da função e métodos.
- Validação executada:
  - backend compila;
  - rotas críticas existem;
  - nenhum endpoint HTTP recebe parâmetro `conn`;
  - docker-compose e portainer-stack são YAML válidos;
  - frontend builda com sucesso.

## V6.7.0

Frontend Material Design:
- Adiciona Material UI / MUI.
- Cria tema dark inspirado em Material Design 3.
- Substitui wrappers visuais principais por MUI:
  - Paper/Card
  - Button
  - Chip
  - Dialog
  - IconButton
  - Typography
- Melhora responsividade do topo, navegação mobile, cards, dialogs e inputs.
- Mantém backend e endpoints intactos.

## V6.7.1 Premium UX

Refinamento frontend-only:
- Redesenho premium/minimalista do dashboard.
- Nova hierarquia visual: próxima ação, métricas, eventos e ações pendentes.
- Cards menores, menos vazios e melhor alinhados.
- Ações pendentes compactas e mais legíveis.
- Eventos futuros com layout mais limpo.
- Responsividade revisada para desktop, tablet e mobile.
- Sem alteração de backend/endpoints.

## V6.7.2 Premium Inventory

Frontend-only:
- Redesenho completo da aba Estoque.
- Formulário reorganizado em seções lógicas.
- Remove botão gigante e layout pesado.
- Checkboxes viram opções compactas.
- Empty state mais profissional.
- Cards de estoque mais premium e legíveis.
- Responsividade revisada para desktop/tablet/mobile.

## V6.7.3 Final Premium Dashboard

Frontend-only:
- Dashboard redesenhado para seguir o mockup premium enviado.
- Remove o cabeçalho/hero “Hoje / Centro de saúde pessoal”.
- Próxima ação vira “Próximo medicamento”.
- Nomes longos de medicamento são resumidos no frontend, ex.: “Aplicação Deposteron”.
- Remove chip “pendente” do card principal.
- Chip de observação, como “lado direito”, fica como informação principal.
- Ações pendentes ficaram mais compactas e menos poluídas.
- Botões ficaram mais claros: Abrir receita, Marcar aplicado, Confirmar agenda, Adiar, Não realizado.
- Layout com KPI strip, painel principal, próximos eventos e insights rápidos.
- Responsividade revisada.

## V6.7.5 Final Premium

Frontend-only:
- Remove a seção inferior de cards rápidos do dashboard:
  - Receitas ativas
  - Adesão ao tratamento
  - Aplicações este mês
- Mantém o dashboard mais limpo e minimalista.


## V6.7.7 Calendar-driven treatment events

Correção crítica:
- Receita importada não cria mais eventos automaticamente por intervalo/data da receita.
- Eventos de aplicação de medicamentos que exigem receita só são criados/vinculados com base em eventos reais do calendário.
- Limpa pendências antigas criadas sem `linked_calendar_event_id` e sem horário real.
- Ao sincronizar o calendário, tratamentos ativos com receita são reconciliados com eventos reais do ICS.


## V6.7.8 Profile fix

Correções:
- Corrige edição/salvamento de perfil.
- Adiciona fluxo explícito de novo perfil quando há perfil em edição.
- Formulário de perfil agora remonta corretamente ao alternar entre perfis.
- Backend valida nome obrigatório e normaliza campos antes de salvar.


## V6.7.9 Review center fix

Correções:
- Central de revisão agora mostra qual documento precisa de revisão.
- Exibe perfil, tipo detectado, status, data e resumo extraído.
- Adiciona ações: Ver PDF, Reprocessar OCR/IA, Marcar revisado e Excluir documento.
- Backend adiciona endpoint `/api/inbox/{id}/resolve`.
- Bootstrap passa a retornar metadados do documento vinculado ao item de revisão.


## V6.8.0 Inventory UX

Ajustes:
- Remove “Controle inteligente” e subtítulo abaixo de Estoque.
- Texto lateral reescrito com foco em cadastro de medicamentos e criação de lembretes.
- Unidade virou dropdown com formatos comuns: unidade, comprimido, cápsula, ampola, caneta, frasco, cartela, caixa, sachê, gota, ml e dose.
- Local da compra removido do formulário principal.
- Frequência virou dropdown com opções 6/8/12h, diário, 3/7/10/15 dias e personalizado.
- Intervalo virou dropdown com opções comuns e personalizado.
- Adicionado campo para vincular receita ao item de estoque.
- Removida legenda final sobre atualização automática do estoque.


## V6.8.1 Smart inventory

Ajustes:
- Quantidade agora é tratada como “total disponível”.
- Adicionado “Baixar por uso” para o sistema saber quanto descontar por dose/aplicação.
- Rotina/frequência reorganizada em presets inteligentes:
  - estoque apenas;
  - diário;
  - 6/8/12h;
  - 2/3/7/10/15/30 dias;
  - personalizado.
- Campo “intervalo” solto removido da UX principal.
- Backend adiciona `dose_quantity` em `medication_inventory` e usa esse valor ao consumir estoque.
- Inferência de intervalo agora entende mais opções de dias e horários.


## V6.8.2 Professional routines

Correções profissionais:
- Rotinas de estoque agora geram pendências reais no MedVault.
- 6/8/12h criam múltiplos horários futuros, não apenas texto.
- Rotinas diárias e por intervalo mantêm eventos futuros automaticamente.
- Ao marcar como tomado/aplicado, o sistema repõe a próxima pendência.
- Scheduler preenche automaticamente as próximas rotinas sem receita.
- Medicamentos que exigem receita continuam baseados em calendário real/ICS, sem eventos artificiais.


## V6.8.4 Purchase/stock import

Implementado:
- Importação de estoque por nota fiscal, cupom ou print de pedido de farmácia.
- Novo botão “Importar nota/print” na aba Estoque.
- Backend extrai texto por OCR de PDF/imagem.
- IA/Ollama extrai farmácia, data, medicamentos, quantidade, unidade, preço e necessidade de receita.
- Fallback local por regex quando IA falhar.
- Tela de revisão antes de salvar.
- Confirmação cria novo item de estoque ou soma ao item existente.
- Histórico de compra e movimentação de estoque são registrados automaticamente.


## V6.8.5 Purchase import fix

Correção:
- Corrige erro 500 ao importar nota/print causado por `safe_filename` ausente.
- Endpoint `/api/inventory/purchase-preview` agora retorna mensagem útil em caso de falha.


## V6.8.6 Purchase quantity fix

Correção:
- Corrige interpretação de pedidos como “Deposteron ... 3 Ampolas 2ml ... 2un”.
- Agora o estoque correto vira 6 ampolas, não 3 ml.
- “2ml” passa a ser tratado como volume/concentração, não unidade de estoque.
- Preço unitário passa a ser calculado sobre a unidade real de estoque.


## V6.8.7 Generic purchase normalizer

Correção:
- Normalizador genérico para nota fiscal/pedido/print.
- Diferencia quantidade comprada, unidades por embalagem e concentração.
- Evita transformar ml/mg em unidade de estoque.
- Exemplos:
  - 3 ampolas 2un = 6 ampolas
  - 30 comprimidos 2un = 60 comprimidos
  - 1 caneta 4un = 4 canetas


## V6.8.8 Stock edit/manual usage

Correções:
- Adicionado botão “Editar” nos itens de estoque.
- Adicionado botão “Registrar uso” para dar baixa manual no estoque.
- Permite corrigir estoque atual, unidade, baixa por uso, alerta mínimo, receita vinculada e observações.
- Backend adiciona `/api/inventory/{id}/consume`.
- `PUT /api/inventory/{id}` agora atualiza quantidade atual e registra movimentação de ajuste.
