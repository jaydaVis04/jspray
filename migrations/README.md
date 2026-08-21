# Database migrations

Migration SQL is packaged under `src/fwtool/migration_sql/` so installed wheels can apply
it. Migrations are append-only and run transactionally in lexical order. Never edit an
already released migration; add a new numbered file.
