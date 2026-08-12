- Vendor name resolution (lookup_vendor, coming in Phase 3) uses Postgres pg_trgm trigram string similarity — no embeddings, no RAG. It's asking "does 'Acme Inc' fuzzy-match a row in the vendors table," which is a structured-data problem, not a semantic-search problem.
- RAG (what we're building now) is for the agent to pull relevant text back from unstructured documents — the invoice's own original wording (in case structured extraction dropped something, like an exact payment term or a handwritten note) and the AP policy doc (e.g. "is there evidence of a second approval for this $15k invoice?"). That's a semantic-search problem — the query and the stored text won't share exact words, so it needs embeddings.

So RAG's job here is narrower than "help organize vendors" — it's specifically the agent's ability to ask "what does the source material actually say" when the structured fields alone aren't enough to make a good decision. Does that distinction make sense before we start?

- so what tools is the agent using is not looking it up? Maybe building a new document/ writing emails, etc. 

- What is alembic?

- What is sqlalchemy?

- why does the vendor data need to be seeded?

- ask about harness.py, what are the specific aspects of the harness design?
