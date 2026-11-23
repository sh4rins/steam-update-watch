# steam-update-watch

Avisa no Discord quando a Steam publica qualquer atualização de um jogo. Hoje vigia o EA SPORTS FC 27 (jogo normal, código 4080220) e o FC 27 Lite (4407750).

## Como funciona

1. O GitHub Actions roda `watch.py` a cada 5 minutos.
2. O script pergunta ao SteamCMD (imagem oficial `steamcmd/steamcmd`, sem login) a versão publicada: número da versão, data de liberação, arquivos (depots) e ramos.
3. Compara com `state.json`. Se mudou, posta no Discord marcando o dono e só então grava o novo estado.
4. Depois das 9h (Fortaleza) manda uma mensagem silenciosa de "monitor ativo".
5. Se a Steam não responder por cerca de 30 minutos, manda um aviso. Quando volta, avisa que voltou.

## Configuração

Em Settings > Secrets and variables > Actions:

| Secret | Conteúdo |
|---|---|
| `DISCORD_WEBHOOK` | Link do webhook do canal que recebe os avisos. |
| `MENTION_USER_ID` | Número do usuário do Discord que será marcado. |

Os jogos vigiados ficam em `config.json`.

## Testar

Em Actions > watch > Run workflow, marque `selftest`. O aviso sai com o prefixo `[TESTE]` e o estado não muda.

## Limites

- O GitHub pode atrasar execuções agendadas em horários de pico, normalmente por poucos minutos.
- O estado é um commit em `state.json` quando algo muda (e uma vez por dia, no sinal de vida), o que também mantém o agendamento ativo.
