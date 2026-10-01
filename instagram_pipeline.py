import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any
from pathlib import Path

import requests
from supabase import Client, create_client

from manus import (
    ManusAPIError,
    criar_tarefa_carrossel,
    enviar_mensagem_tarefa,
    listar_mensagens_tarefa,
)

logger = logging.getLogger("raposa-cacadora.instagram")
INSTAGRAM_BATCH_SIZE = max(1, int(os.getenv("INSTAGRAM_BATCH_SIZE", "5")))
INSTAGRAM_AUTO_BATCH = os.getenv("INSTAGRAM_AUTO_BATCH", "true").strip().lower() in {"1", "true", "yes", "on"}
INSTAGRAM_PENDING_WORKER = os.getenv("INSTAGRAM_PENDING_WORKER", "true").strip().lower() in {"1", "true", "yes", "on"}
INSTAGRAM_PENDING_INTERVAL = max(5, int(os.getenv("INSTAGRAM_PENDING_INTERVAL", "10")))
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID", "").strip()
BOT_ID = os.getenv("BOT_ID", os.getenv("FILA_ORIGEM", "raposa-cacadora")).strip()

def _agora() -> str:
    return datetime.now(timezone.utc).isoformat()

def _legenda_valida(caption: str, quantidade: int, marketplace: str = "shopee") -> bool:
    texto = str(caption or "").strip()
    if not texto or re.search(r"https?://|www\\.", texto, re.I): return False
    proibidos = (r"links? dos achadinhos", r"encontre os links", r"links? na ordem", r"acesse os links", r"link de cada produto")
    if any(re.search(p, texto, re.I) for p in proibidos): return False
    if "@raposacacadora" not in texto or "EU QUERO" not in texto.upper(): return False
    if not re.search(r"1️⃣|1\\.\\s", texto): return False
    if quantidade >= 5 and not re.search(r"5️⃣|5\\.\\s", texto): return False
    hashtags = r"#(?:mercadolivre|achadosmercadolivre|raposacacadora)\\b" if marketplace == "mercadolivre" else r"#(?:achadosshopee|shopee|raposacacadora)\\b"
    return bool(re.search(hashtags, texto, re.I))


def _persistir_attachments_storage(supabase: Client, post_id: int, attachments: list[dict[str, str]]) -> list[dict[str, str]]:
    salvos = []
    base_url = str(os.getenv("SUPABASE_URL") or "").rstrip("/")
    if not base_url: return salvos
    for attachment_index, attachment in enumerate(attachments, start=1):
        url = str(attachment.get("url") or "").strip()
        if not url.startswith(("http://", "https://")): continue
        try:
            resposta = requests.get(url, timeout=60, allow_redirects=True, headers={"User-Agent":"Mozilla/5.0 RaposaCacadora/1.0","Accept":"image/*,*/*;q=0.8"})
            resposta.raise_for_status()
            content_type = (resposta.headers.get("Content-Type") or "").split(";",1)[0].strip().lower()
            if not content_type.startswith("image/"):
                candidatos = _extrair_urls_de_conteudo_manus(resposta.content.decode("utf-8", errors="ignore"), resposta.url)
                for candidato in candidatos:
                    try:
                        rr=requests.get(candidato, timeout=60, allow_redirects=True, headers={"User-Agent":"Mozilla/5.0 RaposaCacadora/1.0","Accept":"image/*,*/*;q=0.8"})
                        tipo=(rr.headers.get("Content-Type") or "").split(";",1)[0].strip().lower()
                        if rr.ok and tipo.startswith("image/") and rr.content:
                            resposta, content_type = rr, tipo
                            break
                    except Exception: pass
            if not content_type.startswith("image/") or not resposta.content: raise RuntimeError(f"conteúdo não é imagem: {content_type or 'content-type ausente'}")
            ext = ".png" if "png" in content_type else ".webp" if "webp" in content_type else ".gif" if "gif" in content_type else ".avif" if "avif" in content_type else ".jpg"
            caminho=f"{BOT_ID}/{post_id}/slide_{attachment_index:02d}{ext}"
            supabase.storage.from_("raposa-carrosseis").upload(caminho,resposta.content,{"content-type":content_type,"upsert":"true"})
            salvos.append({"attachment_index":attachment_index,"storage_path":caminho,"storage_url":f"{base_url}/storage/v1/object/public/raposa-carrosseis/{caminho}","content_type":content_type,"file_name":attachment.get("file_name") or f"slide_{attachment_index:02d}{ext}"})
            logger.info("Imagem persistida no Storage: carrossel=%s anexo=%s bytes=%s",post_id,attachment_index,len(resposta.content))
        except Exception as exc:
            logger.exception("Falha ao persistir imagem Manus: carrossel=%s anexo=%s erro=%s",post_id,attachment_index,exc)
    return salvos

def _normalizar_legenda(caption: str, produtos: list[dict[str, Any]], marketplace: str = "shopee") -> str:
    texto = str(caption or "").strip()
    texto = re.sub(r"https?://\\S+", "", texto)
    texto = re.sub(r"\\n?Links? dos achadinhos:?\\s*", "", texto, flags=re.I)
    texto = re.sub(r"\\n?Encontre os links[^\\n]*", "", texto, flags=re.I)
    texto = re.sub(r"\\n?Acesse os links[^\\n]*", "", texto, flags=re.I)
    texto = re.sub(r"\\n?Links? na ordem[^\\n]*", "", texto, flags=re.I)
    texto = re.sub(r"\\n{3,}", "\\n\\n", texto).strip()
    nomes = [str(p.get("productName") or p.get("product_name") or p.get("name") or "Achadinho").strip() for p in produtos]
    if not re.search(r"1️⃣|1\\.\\s", texto):
        texto += "\\n\\n" + "\\n".join(f"{i}️⃣ {nome}" for i, nome in enumerate(nomes, 1))
    if "EU QUERO" not in texto.upper() or "@raposacacadora" not in texto:
        texto += '\\n\\nTudo que você tá vendo aqui tá com LINK NA BIO e nos STORIES! 👇\\n\\n👉 Curte se você amou.\\n👉 Segue @raposacacadora pra não perder nenhum achado.\\n👉 Comenta "EU QUERO" que te mando todos os links no direct. 💌'
    hashtags = r"#(?:mercadolivre|achadosmercadolivre|raposacacadora)\\b" if marketplace == "mercadolivre" else r"#(?:achadosshopee|shopee|raposacacadora)\\b"
    if not re.search(hashtags, texto, re.I):
        texto += "\\n\\n#mercadolivre #achadosmercadolivre #raposacacadora" if marketplace == "mercadolivre" else "\\n\\n#achadosshopee #shopee #raposacacadora"
    return texto


def _marketplace_from_link(link: str) -> str:
    texto = str(link or "").lower()
    if any(dominio in texto for dominio in ("mercadolivre.com.br", "mercadolibre.com", "meli.la")):
        return "mercadolivre"
    return "shopee"


def criar_lote_instagram(supabase: Client, produto_ids: list[int], source_chat_id: str | None = None, source_message_id: int | None = None, bot_id: str | None = None) -> int | None:
    if not produto_ids:
        return None
    ids = list(dict.fromkeys(int(x) for x in produto_ids))
    bot_id = (bot_id or BOT_ID).strip()
    primeiro_post_id = None
    rows = supabase.table("produtos_fila").select("id,link").in_("id", ids).eq("bot_id", bot_id).eq("fila_origem", bot_id).execute().data or []
    por_plataforma = {"shopee": [], "mercadolivre": []}
    for row in rows:
        por_plataforma[_marketplace_from_link(row.get("link"))].append(int(row["id"]))
    for plataforma, plataforma_ids in por_plataforma.items():
        for inicio in range(0, len(plataforma_ids), INSTAGRAM_BATCH_SIZE):
            grupo = plataforma_ids[inicio:inicio + INSTAGRAM_BATCH_SIZE]
            if len(grupo) < INSTAGRAM_BATCH_SIZE:
                logger.info("Grupo Instagram %s aguardando completar %d produtos: %d/%d.", plataforma, INSTAGRAM_BATCH_SIZE, len(grupo), INSTAGRAM_BATCH_SIZE)
                continue
            post = supabase.table("instagram_posts").insert({"status": "pending", "bot_id": bot_id, "source_chat_id": source_chat_id, "source_message_id": source_message_id}).execute()
            if not post.data:
                raise RuntimeError("Não foi possível criar o lote Instagram.")
            post_id = int(post.data[0]["id"])
            primeiro_post_id = primeiro_post_id or post_id
            supabase.table("instagram_post_products").insert([{"instagram_post_id": post_id, "produto_fila_id": produto_id, "position": position} for position, produto_id in enumerate(grupo, start=1)]).execute()
            logger.info("Lote Instagram #%s criado com %d produto(s), plataforma=%s.", post_id, len(grupo), plataforma)
    return primeiro_post_id

def registrar_produto_processado(supabase: Client, produto_id: int, produto: dict[str, Any]) -> list[int]:
    relacionamentos=supabase.table("instagram_post_products").select("id,instagram_post_id").eq("produto_fila_id",produto_id).execute(); post_ids=[]; snapshot=dict(produto); snapshot.pop("raw_response",None)
    for rel in relacionamentos.data or []:
        supabase.table("instagram_post_products").update({"product_snapshot":snapshot}).eq("id",rel["id"]).execute(); post_ids.append(int(rel["instagram_post_id"]))
    return post_ids

def _buscar_produtos_do_lote(supabase: Client, post_id: int) -> list[dict[str, Any]]:
    """Nunca deixa um lote completo travado só porque um snapshot não foi salvo."""
    rows=supabase.table("instagram_post_products").select("position,product_snapshot,produto_fila_id").eq("instagram_post_id",post_id).order("position").execute(); produtos=[]
    for row in rows.data or []:
        produto=dict(row.get("product_snapshot") or {})
        if not produto:
            fallback=supabase.table("produtos_fila").select("*").eq("id",row.get("produto_fila_id")).limit(1).execute()
            if fallback.data: produto=dict(fallback.data[0]); logger.warning("Snapshot ausente no lote #%s; usando produto_fila #%s como fallback.",post_id,row.get("produto_fila_id"))
        if produto:
            produto["id"]=row.get("produto_fila_id"); produtos.append(produto)
    return produtos

def _extrair_attachments(detail: dict[str, Any], messages: dict[str, Any] | None = None) -> list[dict[str, str]]:
    encontrados=[]; vistos=set()
    def adicionar(item: Any):
        if not isinstance(item,dict): return
        url=str(item.get("url") or item.get("download_url") or item.get("downloadUrl") or "").strip()
        if not url.startswith(("https://","http://")): return
        content_type=str(item.get("content_type") or item.get("mime_type") or item.get("mimeType") or "").strip().lower(); nome=str(item.get("file_name") or item.get("filename") or item.get("name") or "").strip(); path=str(item.get("path") or "").strip(); nome_l=nome.lower()
        if nome_l.endswith((".json",".txt",".md",".html")): return
        parece=bool(nome or path or content_type or item.get("file_uid") or item.get("version_uid") or item.get("type") in {"image","file","slides"})
        if not parece or (content_type and not content_type.startswith("image/")): return
        if not nome: nome=Path(path).name if path else "imagem"
        chave=(nome,url)
        if chave not in vistos: vistos.add(chave); encontrados.append({"file_name":nome,"url":url,"path":path,"content_type":content_type or "image/*"})
    def percorrer(value: Any):
        if isinstance(value,dict): adicionar(value); [percorrer(v) for v in value.values()]
        elif isinstance(value,list): [percorrer(v) for v in value]
    percorrer(detail); percorrer(messages or {})
    encontrados.sort(key=lambda item:(0 if "capa" in item["file_name"].lower() else 1,item["file_name"].lower()))
    return encontrados

def _extrair_file_ids_telegram(resposta: requests.Response) -> list[str]:
    ids=[]
    try:
        for msg in resposta.json().get("result",[]):
            photos=msg.get("photo") or []
            if photos: ids.append(str(photos[-1].get("file_id") or ""))
    except Exception: logger.exception("Não foi possível extrair file_ids do Telegram.")
    return ids

def _extrair_urls_de_conteudo_manus(conteudo: str, base_url: str | None = None) -> list[str]:
    urls=[]; vistos=set()
    for padrao in [r"!\[[^\]]*\]\((https?://[^)\s]+)",r"<img[^>]+src=[\"'](https?://[^\"']+)",r"https?://[^\s)\"'<>]+"]:
        for encontrado in re.findall(padrao,conteudo or "",flags=re.I):
            url=str(encontrado).strip().rstrip(".,;")
            if url.startswith(("http://","https://")) and url not in vistos: vistos.add(url); urls.append(url)
    return urls

def _baixar_imagem_para_telegram(url: str, _tentativa: int = 0) -> tuple[bytes, str]:
    resposta=requests.get(url,timeout=45,allow_redirects=True,headers={"User-Agent":"Mozilla/5.0 RaposaCacadora/1.0","Accept":"image/*,*/*;q=0.8"}); resposta.raise_for_status(); conteudo=resposta.content
    if not conteudo: raise RuntimeError("arquivo vazio")
    tipo=(resposta.headers.get("Content-Type") or "").split(";",1)[0].strip().lower()
    if tipo.startswith("image/"): return conteudo,tipo
    if _tentativa==0 and tipo in {"text/markdown","text/html","text/plain",""}:
        for candidato in _extrair_urls_de_conteudo_manus(conteudo.decode("utf-8",errors="ignore"),resposta.url):
            try: return _baixar_imagem_para_telegram(candidato,1)
            except Exception: pass
    raise RuntimeError(f"conteúdo não é imagem: {tipo or 'content-type ausente'}")

def _enviar_grupo_telegram_por_arquivo(base: str, chat_id: int, grupo: list[dict[str,str]], post_id: int, caption: str, inicio: int) -> requests.Response:
    media=[]; files={}
    try:
        for pos,item in enumerate(grupo):
            nome=item.get("file_name") or f"carrossel-{post_id}-{inicio+pos+1}.jpg"; chave=f"file{pos}"; conteudo,tipo=_baixar_imagem_para_telegram(item["url"]); media_item={"type":"photo","media":f"attach://{chave}"}
            if inicio==0 and pos==0: media_item["caption"]=f"🦊 <b>CARROSSEL #{post_id}</b>\\n\\n{caption[:900]}"; media_item["parse_mode"]="HTML"
            media.append(media_item); files[chave]=(nome,conteudo,tipo)
        return requests.post(f"{base}/sendMediaGroup",data={"chat_id":str(chat_id),"media":json.dumps(media,ensure_ascii=False)},files=files,timeout=90)
    finally: files.clear()

def _enviar_preview_telegram(post_id: int, detail: dict[str, Any], attachments: list[dict[str, str]], caption: str, task_url: str | None, supabase: Client | None = None) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_ADMIN_ID or not attachments: logger.warning("Preview Telegram não enviado: token/chat/attachments ausente."); return False
    base=f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"; chat_id=int(TELEGRAM_ADMIN_ID); enviados=0; file_ids=[]
    for inicio in range(0,len(attachments),10):
        grupo=attachments[inicio:inicio+10]; media=[{"type":"photo","media":item["url"]} for item in grupo]
        if inicio==0 and media: media[0].update({"caption":f"🦊 <b>CARROSSEL #{post_id}</b>\\n\\n{caption[:900]}","parse_mode":"HTML"})
        try: resposta=requests.post(f"{base}/sendMediaGroup",json={"chat_id":chat_id,"media":media},timeout=60)
        except Exception as exc: logger.warning("Falha de rede no preview #%s: %s",post_id,exc); resposta=None
        if resposta is not None and resposta.ok: file_ids.extend(_extrair_file_ids_telegram(resposta)); enviados+=len(grupo); continue
        try: resposta_arquivo=_enviar_grupo_telegram_por_arquivo(base,chat_id,grupo,post_id,caption,inicio)
        except Exception as exc: logger.error("Upload direto falhou no lote #%s: %s",post_id,exc); return False
        if not resposta_arquivo.ok: logger.error("Telegram recusou arquivos do lote #%s: %s",post_id,resposta_arquivo.text[:1000]); return False
        file_ids.extend(_extrair_file_ids_telegram(resposta_arquivo)); enviados+=len(grupo)
    if supabase and file_ids: _salvar_file_ids(supabase,post_id,file_ids)
    texto=f"🦊 <b>CARROSSEL #{post_id} RECEBIDO DA MANUS</b>\\n\\n📸 <b>{enviados}</b> imagem(ns) enviadas acima.\\n👀 Revise a capa e os produtos antes da publicação.\\nEscolha uma opção abaixo:"
    if task_url: texto+=f'\\n\\n🔗 <a href="{task_url}">Abrir tarefa no Manus</a>'
    resposta=requests.post(f"{base}/sendMessage",json={"chat_id":chat_id,"text":texto,"parse_mode":"HTML","disable_web_page_preview":True,"reply_markup":{"inline_keyboard":[[{"text":"✅ APROVAR","callback_data":f"carousel_approve:{post_id}"},{"text":"❌ REPROVAR","callback_data":f"carousel_reject:{post_id}"}]]}},timeout=30)
    return resposta.ok

def processar_lote_se_pronto(supabase: Client, post_id: int) -> bool:
    post_response=supabase.table("instagram_posts").select("*").eq("id",post_id).eq("bot_id",BOT_ID).limit(1).execute()
    if not post_response.data or post_response.data[0].get("status")!="pending": return False
    produtos=_buscar_produtos_do_lote(supabase,post_id)
    if len(produtos)<INSTAGRAM_BATCH_SIZE: logger.warning("Lote #%s incompleto: %d/%d produtos disponíveis.",post_id,len(produtos),INSTAGRAM_BATCH_SIZE); return False
    produtos=produtos[:INSTAGRAM_BATCH_SIZE]
    reservado=supabase.table("instagram_posts").update({"status":"manus_processing","updated_at":_agora()}).eq("id",post_id).eq("status","pending").execute()
    if not reservado.data: return False
    try:
        marketplaces = {str(x.get("marketplace") or "").strip().lower() for x in produtos}
        marketplaces.discard("")
        if not marketplaces:
            marketplaces = {_marketplace_from_link(x.get("link")) for x in produtos}
        if len(marketplaces) != 1:
            raise ManusAPIError("CAROUSEL_MIXED_MARKETPLACES: lote contém plataformas diferentes.")
        resultado=criar_tarefa_carrossel(produtos,post_id,next(iter(marketplaces))); task=resultado.get("task_detail") or resultado.get("task") or {}; task_id=task.get("task_id") or resultado.get("task_id"); task_url=task.get("task_url") or resultado.get("task_url")
        if not task_id: raise ManusAPIError(f"Manus não retornou task_id: {resultado}")
        supabase.table("instagram_posts").update({"manus_task_id":task_id,"manus_task_url":task_url,"prompt":"Carrossel Instagram criado automaticamente pela pipeline; preview enviado ao Telegram quando concluído.","updated_at":_agora()}).eq("id",post_id).execute()
        logger.info("Lote Instagram #%s enviado para Manus: %s",post_id,task_id); return True
    except Exception as exc:
        supabase.table("instagram_posts").update({"status":"error","error":str(exc)[:4000],"updated_at":_agora()}).eq("id",post_id).execute(); logger.exception("Falha no lote Instagram #%s.",post_id); return False

def processar_lotes_pendentes(supabase: Client, limite: int = 10, bot_id: str | None = None) -> int:
    bot_id=(bot_id or BOT_ID).strip(); resposta=supabase.table("instagram_posts").select("id").eq("status","pending").eq("bot_id",bot_id).order("id").limit(limite).execute(); enviados=0
    for row in resposta.data or []:
        try:
            if processar_lote_se_pronto(supabase,int(row["id"])): enviados+=1
        except Exception: logger.exception("Erro no worker Instagram lote #%s",row.get("id"))
    return enviados

def _worker_lotes_pendentes() -> None:
    if not INSTAGRAM_PENDING_WORKER: return
    url=os.getenv("SUPABASE_URL","").strip(); key=os.getenv("SUPABASE_KEY","").strip()
    if not url or not key: logger.warning("Worker Instagram não iniciado: SUPABASE_URL/SUPABASE_KEY ausentes."); return
    try: cliente=create_client(url,key)
    except Exception: logger.exception("Não foi possível criar cliente Supabase do worker Instagram."); return
    logger.info("Worker de lotes Instagram pendentes iniciado; intervalo=%ss.",INSTAGRAM_PENDING_INTERVAL)
    while True:
        try:
            quantidade=processar_lotes_pendentes(cliente,limite=10,bot_id=BOT_ID)
            if quantidade: logger.info("Worker Instagram enviou %d lote(s) pendente(s) para o Manus.",quantidade)
        except Exception: logger.exception("Erro no worker de lotes Instagram pendentes.")
        time.sleep(INSTAGRAM_PENDING_INTERVAL)

def iniciar_worker_lotes_pendentes() -> None:
    if INSTAGRAM_PENDING_WORKER: threading.Thread(target=_worker_lotes_pendentes,name="instagram-pending-worker",daemon=True).start()

def processar_webhook_manus(supabase: Client, payload: dict[str, Any]) -> tuple[bool,str]:
    event_type = payload.get("event_type")
    detail = payload.get("task_detail") or {}
    task_id = detail.get("task_id")
    if not task_id:
        return False, "task_id ausente"

    post_response = (
        supabase.table("instagram_posts")
        .select("id,status,bot_id,error")
        .eq("manus_task_id", task_id)
        .eq("bot_id", BOT_ID)
        .limit(1)
        .execute()
    )
    if not post_response.data:
        return True, "tarefa ignorada: task_id não pertence a esta pipeline"

    post_id = int(post_response.data[0]["id"])
    status_atual = str(post_response.data[0].get("status") or "").strip().lower()

    if event_type == "task_created":
        return True, "task_created registrado"

    # Após a aprovação, a próxima conclusão da tarefa Manus é a execução da publicação.
    # O preview continua sendo marcado como ready na conclusão inicial da geração.
    if event_type == "task_stopped" and detail.get("stop_reason") == "finish" and status_atual == "approved":
        mensagem = str(detail.get("message") or "").strip()
        mensagem_lower = mensagem.lower()
        failure_markers = (
            "não consegui", "nao consegui", "não foi possível", "nao foi possivel",
            "falha", "erro", "failed", "failure", "couldn't", "could not", "unable",
        )
        publication_markers = (
            "publicad", "postad", "published", "posted", "instagram.com/",
            "publicação concluída", "publicacao concluida", "post publicado",
        )

        if any(marker in mensagem_lower for marker in failure_markers):
            erro = mensagem or "O Manus informou falha na publicação."
            supabase.table("instagram_posts").update({
                "status": "error",
                "error": erro[:4000],
                "updated_at": _agora(),
            }).eq("id", post_id).eq("bot_id", BOT_ID).execute()
            logger.error("Publicação do carrossel #%s falhou segundo o Manus. Status -> error.", post_id)
            return True, "publicação marcada como erro"

        if any(marker in mensagem_lower for marker in publication_markers):
            supabase.table("instagram_posts").update({
                "status": "published",
                "published_at": _agora(),
                "error": None,
                "updated_at": _agora(),
            }).eq("id", post_id).eq("bot_id", BOT_ID).execute()
            logger.info("Publicação confirmada pelo Manus para o carrossel #%s. Status -> published.", post_id)
            return True, "publicação confirmada; status atualizado para published"

        structured = detail.get("structured_output") or {}
        value = structured.get("value") or {}
        texto_structured = json.dumps(value, ensure_ascii=False).lower()
        if any(marker in texto_structured for marker in publication_markers):
            supabase.table("instagram_posts").update({
                "status": "published",
                "published_at": _agora(),
                "error": None,
                "updated_at": _agora(),
            }).eq("id", post_id).eq("bot_id", BOT_ID).execute()
            logger.info("Publicação confirmada pelo structured output do Manus para o carrossel #%s.", post_id)
            return True, "publicação confirmada via structured output"

        logger.warning(
            "Tarefa aprovada #%s terminou sem confirmação explícita de publicação; mantendo status approved.",
            post_id,
        )
        return True, "tarefa concluída sem confirmação explícita de publicação"

    if event_type == "task_stopped" and detail.get("stop_reason") == "finish":
        try:
            structured = detail.get("structured_output") or {}
            if not structured.get("success", False):
                erro_structured = str(
                    structured.get("error") or "Manus não retornou structured output."
                )
                erro_anterior = str(post_response.data[0].get("error") or "")
                erro_lower = erro_structured.lower()
                erro_transitorio = any(
                    marcador in erro_lower
                    for marcador in (
                        "manus_http_500", "node server request failed",
                        "resource temporarily unavailable", "readerror",
                        "internal server error",
                    )
                )
                # Tenta recuperar uma única vez falhas internas/transitórias de extração.
                if erro_transitorio and "AUTO_RETRY_MANUS_500" not in erro_anterior:
                    mensagem_retry = (
                        "A primeira finalização sofreu um erro interno/transitório ao extrair "
                        "o resultado estruturado. Retome esta mesma tarefa, preserve o padrão "
                        "visual das tarefas anteriores e conclua a entrega. Não comece do zero "
                        "se as imagens já tiverem sido criadas. Retorne category, subcategory, "
                        "concept, caption e slides no formato JSON solicitado."
                    )
                    enviar_mensagem_tarefa(task_id, mensagem_retry)
                    supabase.table("instagram_posts").update({
                        "status": "manus_processing",
                        "error": "AUTO_RETRY_MANUS_500: " + erro_structured[:3500],
                        "updated_at": _agora(),
                    }).eq("id", post_id).eq("bot_id", BOT_ID).execute()
                    logger.warning(
                        "Solicitada uma única recuperação automática para erro interno Manus no carrossel #%s.",
                        post_id,
                    )
                    return True, "recuperação automática solicitada ao Manus"
                raise ManusAPIError(erro_structured)
            value = structured.get("value") or {}
            produtos = _buscar_produtos_do_lote(supabase, post_id)
            caption = str(value.get("caption") or "")
            marketplaces = {str(x.get("marketplace") or "").strip().lower() for x in produtos}
            marketplaces.discard("")
            if not marketplaces:
                marketplaces = {_marketplace_from_link(x.get("link")) for x in produtos}
            if len(marketplaces) != 1:
                raise ManusAPIError("CAROUSEL_MIXED_MARKETPLACES: lote contém plataformas diferentes.")
            marketplace = next(iter(marketplaces))
            if not _legenda_valida(caption, len(produtos), marketplace):
                enviar_mensagem_tarefa(
                    task_id,
                    "A legenda retornada está fora do padrão obrigatório da Raposa Caçadora. "
                    "REFAÇA SOMENTE o campo caption. Não publique nada ainda; aguarde a validação da legenda corrigida.",
                )
                return True, "legenda inválida; correção solicitada ao Manus"

            attachments = _extrair_attachments(detail)
            try:
                mensagens_manus = listar_mensagens_tarefa(task_id)
                attachments_historico = _extrair_attachments({}, mensagens_manus)
                existentes = {item["url"] for item in attachments}
                attachments.extend(
                    item for item in attachments_historico
                    if item["url"] not in existentes
                )
            except Exception:
                logger.exception("Não foi possível recuperar attachments da tarefa Manus %s", task_id)

            existentes = {item["url"] for item in attachments}
            for slide in value.get("slides") or []:
                if isinstance(slide, dict):
                    url = str(slide.get("asset_url") or slide.get("image_url") or "").strip()
                    if url.startswith(("http://", "https://")) and url not in existentes:
                        attachments.append({
                            "file_name": f"slide_{len(attachments)+1:02d}.jpg",
                            "url": url,
                            "path": "",
                            "content_type": "image/*",
                        })
                        existentes.add(url)

            if len(attachments) < INSTAGRAM_BATCH_SIZE + 1:
                raise ManusAPIError(
                    f"MANUS_IMAGES_INCOMPLETE: {len(attachments)} imagem(ns) encontradas; "
                    f"esperado pelo menos {INSTAGRAM_BATCH_SIZE + 1} (capa + produtos)."
                )

            caption = _normalizar_legenda(caption, produtos, marketplace)
            assets = value.get("slides") if isinstance(value.get("slides"), list) else []
            assets = [dict(item) if isinstance(item, dict) else {"asset_url": str(item)} for item in assets]
            storage_assets = _persistir_attachments_storage(supabase, post_id, attachments)
            if len(storage_assets) < INSTAGRAM_BATCH_SIZE + 1:
                raise ManusAPIError(
                    f"STORAGE_IMAGES_INCOMPLETE: {len(storage_assets)} imagem(ns) persistidas; "
                    f"esperado {INSTAGRAM_BATCH_SIZE + 1}."
                )

            for idx, salvo in enumerate(storage_assets):
                if idx >= len(assets):
                    assets.append({})
                assets[idx].update({
                    "position": idx + 1,
                    "image_url": salvo["storage_url"],
                    "storage_url": salvo["storage_url"],
                    "storage_path": salvo["storage_path"],
                    "file_name": salvo["file_name"],
                    "content_type": salvo["content_type"],
                })

            supabase.table("instagram_posts").update({
                "status": "ready",
                "category": value.get("category"),
                "caption": caption,
                "assets": assets,
                "manus_result": structured,
                "error": None,
                "updated_at": _agora(),
            }).eq("id", post_id).execute()

            enviados = _enviar_preview_telegram(
                post_id,
                detail,
                [{"file_name": x["file_name"], "url": x["storage_url"], "content_type": x["content_type"]} for x in storage_assets],
                caption,
                str(detail.get("task_url") or "") or None,
                supabase,
            )
            return (
                True,
                "lote pronto e preview enviado ao Telegram"
                if enviados else
                "lote pronto; preview Telegram não enviado"
            )
        except Exception as exc:
            supabase.table("instagram_posts").update({
                "status": "error",
                "error": str(exc)[:4000],
                "updated_at": _agora(),
            }).eq("id", post_id).execute()
            logger.exception("Falha ao recuperar resultado Manus lote #%s", post_id)
            return False, "falha ao recuperar resultado Manus"

    return True, "evento ignorado"

iniciar_worker_lotes_pendentes()
