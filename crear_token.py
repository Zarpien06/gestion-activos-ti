import hashlib, secrets
from supabase_client import supabase
token=secrets.token_urlsafe(32)
supabase.table("tokens_recolector").insert({"nombre":"Recolector inicial","token_hash":hashlib.sha256(token.encode()).hexdigest(),"activo":True}).execute()
print("TOKEN (guardalo ahora):",token)
