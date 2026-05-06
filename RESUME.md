# Resume Prompt

Copia e incolla questo come primo messaggio della prossima sessione:

---

Leggi CLAUDE.md, la memoria del progetto (tutti i file in memory/), e il git log recente. Poi riprendiamo da dove ci siamo fermati.

256/256 test passano. Il progetto è funzionante con Docker. Container sani.

## Cosa è stato fatto nella sessione 9 (5 maggio)

### Commit della sessione
1. `7cdd1b2` — feat: add User.preferences JSONB column and AuditLog model
2. `0d7357c` — feat: add PATCH /api/preferences endpoint for theme persistence
3. `5d0622b` — feat: add audit_service.log_action() with action labels
4. `16bfef3` — feat: add dark mode with toggle, localStorage, and CSS custom properties
5. `1d44cb5` — feat: add admin audit log page with filters and pagination
6. `2eea2fe` — feat: wire audit logging into admin and config operations
7. `6330e53` — feat: wire audit logging into account and sync operations
8. `8e37ac3` — docs: update README with dark mode and audit logging features
9. `e5ce8b7` — feat: enable full-text search via Dovecot fts-flatcurve
10. `fed20a5` — fix: configure Roundcube to search body via FTS and avoid multi-folder crash

### Dark Mode
- Toggle sun/moon nel sidebar brand area (top right)
- Persistenza: localStorage (no flash al reload) + `User.preferences` JSONB in DB (sync cross-device)
- `PATCH /api/preferences` endpoint con validazione Pydantic
- 24 CSS custom properties in `:root` + `[data-theme="dark"]` override
- Tutti i colori hardcoded sostituiti con `var()` references
- Jinja2 global `get_theme(request)` legge preferenza utente dal DB
- Pico CSS gestisce il grosso, noi solo i colori custom (badge, danger, info box, stat card, ecc.)

### Audit Logging
- `AuditLog` model: timestamp, user_id (SET NULL on delete), username (denormalizzato), action, resource_type/id/name, ip_address, details JSONB
- `services/audit_service.py`: `log_action()` + `ACTION_LABELS` dict (27 azioni) + `get_action_label()`
- Admin viewer a `/admin/audit`: tabella paginata (50/page), filtri HTMX per utente/azione/date range
- Sidebar link con icona `scroll-text`
- Wired su tutte le operazioni admin (user/store/group CRUD, password reset, migrate) + account (create/edit/delete/suspend/migrate/ownership) + sync trigger + config export/import
- Alembic migration 005: `preferences` column + `audit_logs` table

### Full-Text Search
- `docker/dovecot/conf.d/mfb-fts.conf`: abilita fts-flatcurve (Xapian embedded, già nel Docker image)
- `fts_search_add_missing = yes`: auto-indicizza al primo search
- Roundcube configurato con `search_mods` per cercare nel body (triggera FTS)
- Workaround: `search_scope = 'base'` per evitare crash multi-folder di Roundcube 1.6.15
- Testato: 300ms per cercare in 5683 messaggi

## Decisione pendente — Apache Tika

Dovecot 2.4 ha `fts_decoder_tika_url` built-in. Aggiungendo un container `apache/tika:latest` e una riga di config, FTS cercherebbe dentro allegati PDF, Word, Excel, PowerPoint, OpenDocument, HTML, archivi ZIP, ecc.

**Pro:** Zero Dockerfile custom, immagine ufficiale Apache, copre tutti i formati documento comuni
**Contro:** ~200MB RAM (Java), un container in più da gestire
**Immagini (OCR):** Solo metadati EXIF senza Tesseract. Non vale la pena per ora.

Mail-archiver (il competitor C#) NON cerca negli allegati — saremmo i primi.

## Prossima sessione — Feature candidates

1. **Tika attachment search** — Decisione pendente (vedi sopra)
2. **Export mbox/EML** — Data portability
3. **Import mbox/EML** — Migrate existing archives
4. **Retention policies** — Auto-delete from source after N days
5. **i18n** — Multi-language
6. **Sender analysis / stats** — Charts
7. **Mail restore** — Push archived mail back to source
