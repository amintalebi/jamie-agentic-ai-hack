FROM pgvector/pgvector:pg17

# Scripts in this folder run once, when the data volume is first created.
COPY init/ /docker-entrypoint-initdb.d/
