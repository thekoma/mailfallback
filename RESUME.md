# Resume Prompt

Copia e incolla questo come primo messaggio della prossima sessione:

---

Leggi CLAUDE.md, la memoria del progetto (tutti i file in memory/), e il git log recente. Poi riprendiamo da dove ci siamo fermati.

286/286 test passano. Il progetto è funzionante con Docker. Container sani. PostgreSQL 18.

## Cosa è stato fatto nella sessione 10 (6-7 maggio)

### Infra
- Migration 006: 4 tabelle mancanti (`groups`, `user_allowed_stores`, `group_members`, `account_groups`) — bug segnalato da Ivan
- Test anti-drift Alembic (`test_alembic_sync.py`) + pre-commit hook `alembic-drift`
- Upgrade PostgreSQL 16→18 con dump/restore
- 4 PR Renovate mergiate (Python 3.14, setup-uv v8, gh-release v3, PG18)

### Mail Restore (feature completa)
- `RestoreJob` model + migration 007 + `RestoreMode` enum
- `restore_service.py` — CRUD con validazione (suspended/migrating/duplicati/credentials)
- `restore_worker.py` — IMAP read da Dovecot locale → APPEND su server remoto
  - Retry con backoff [1, 3, 10]s
  - OAuth2 token refresh per target
  - Cancel support via flag set
  - Auto-reconnect su broken pipe / `IMAP4.abort` (sia source che target)
  - Detect separatore gerarchico del target (`/` vs `.`)
  - Escape collision separatore nei nomi folder (`unroll.me` → `unroll_me`)
  - Audit log su completamento/fallimento
- `dovecot_auth.py` — utenti Dovecot effimeri con password random, scoped per account, cleanup al boot
- REST API: `POST /api/restore`, `GET /api/restore/{id}`, `POST /api/restore/{id}/cancel`
- Browse API: `GET /api/accounts/{id}/mailboxes`, `.../messages`, `.../search`
- UI pagina `/restore` con wizard HTMX:
  - 3 mode: Full restore / Select folders / Search & pick
  - Folder browser con conteggio messaggi
  - Ricerca avanzata Roundcube-style: toggle Subject/Sender(From,Reply-To,Followup-To)/Recipient(To,Cc,Bcc)/Body/Entire message
  - Filtri Type (All/Unread/Flagged/Unanswered/Deleted/With attachment)
  - Date range (Since/Before)
  - Scope (Current folder / All folders)
  - Multi-word AND search (ogni parola cercata separatamente)
  - MIME subject decoding (`=?UTF-8?Q?...?=` → testo leggibile)
  - Tabella risultati: sort per colonna, select all, column toggle (Subject/From/To/Date/Folder/Message-ID), colonne ridimensionabili con drag
  - Folder mapping: Original / Restored/ prefix / Custom prefix
  - Custom prefix input per folder di destinazione
  - Search feedback: spinner + bottone disabilitato durante la ricerca
  - Progress bar HTMX polling ogni 2s con cancel
  - Status badge: verde (completed) / giallo amber (partial) / rosso (failed)
  - Restore History con badge colorati

### Commit della sessione (37 commit)
Troppi per listarli tutti — vedi `git log --oneline --since="2026-05-06"`.

## Bug noti e TODO per sessione 11

### Bug da fixare
1. **Cache Dovecot corrotta** — UID 97 in Archive di Live ha dimensione mismatch. Workaround: `doveadm force-resync -u koma "*"`. Serve un bottone admin per triggerare il resync da UI.

### TODO prioritari
1. **Admin: Dovecot mailbox health check** — Bottone in admin per verificare integrità caselle (`doveadm force-resync`), mostrare errori, e triggerare fix
2. **Admin: FTS reindex** — Bottone per triggerare `doveadm fts rescan` e `doveadm index` da UI, al momento non c'è modo di reindicizzare senza CLI
3. **Restore: warning separatore** — Quando il target usa `.` come separatore, mostrare warning UI prima del restore con lista folder che verranno rinominate + input per scegliere il carattere di escape (default `_`)
4. **Restore: test end-to-end reale** — Verificare un full restore Live→Molotov ora che il reconnect e il separator fix sono in place

### Feature backlog
1. **Tika attachment search** — Decisione ancora pendente (container Java ~200MB)
2. **Export mbox/EML** — Data portability
3. **Import mbox/EML** — Migrate existing archives
4. **Retention policies** — Auto-delete from source after N days
5. **i18n** — Multi-language
6. **Sender analysis / stats** — Charts
