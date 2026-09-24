import json
import logging
import os
from typing import Any

import requests

logger = logging.getLogger("raposa-cacadora.manus")
MANUS_API_URL = os.getenv("MANUS_API_URL", "https://api.manus.ai").rstrip("/")
MANUS_API_KEY = os.getenv("MANUS_API_KEY", "").strip()

class ManusAPIError(RuntimeError):
    pass

def _headers() -> dict[str, str]:
    if not MANUS_API_KEY:
        raise ManusAPIError("MANUS_API_KEY não configurada.")
    return {"Content-Type": "application/json", "x-manus-api-key": MANUS_API_KEY}

def criar_tarefa_carrossel(produtos: list[dict[str, Any]], post_id: int) -> dict[str, Any]:
    if not produtos:
        raise ManusAPIError("Nenhum produto disponível para a tarefa.")
    produtos_publicos = []
    for produto in produtos:
        produtos_publicos.append({
            "id": produto.get("id"),
            "name": produto.get("productName") or produto.get("product_name") or "Produto",
            "price": produto.get("priceMin") or produto.get("price") or 0,
            "discount_rate": produto.get("priceDiscountRate") or 0,
            "rating": produto.get("ratingStar") or 0,
            "sales": produto.get("sales") or 0,
            "shop_name": produto.get("shopName") or "Loja Shopee",
            "image_url": produto.get("imageUrl") or produto.get("image_url") or "",
            "affiliate_url": produto.get("affiliateLink") or produto.get("manualAffiliateLink") or produto.get("link") or "",
        })
    prompt = f"""
Você é o criador visual e copywriter da Raposa Caçadora, especializado em carrosséis virais para Instagram.

Crie um carrossel vertical 4:5 (1080x1350) com {len(produtos_publicos)} produtos da Shopee.
O carrossel deve ter EXATAMENTE 1 capa + 1 slide para cada produto.
Idioma: português do Brasil.

DIREÇÃO VISUAL OBRIGATÓRIA
- Estética: Pinterest viral, cozy aesthetic, elegante e fotorealista.
- Formato de TODAS as imagens: vertical 4:5.
- Alta resolução, composição limpa, iluminação natural/quente e aparência de fotografia profissional.
- Não usar mockups genéricos, ilustrações, aparência 3D artificial ou imagens de banco claramente genéricas.
- Quando houver imagem do produto nos dados, use-a como referência e preserve fielmente o modelo, formato, materiais, cores e detalhes do produto. NÃO invente outro modelo.
- Não inserir botões, emojis gráficos, ícones de interface, molduras de aplicativo, preço falso ou elementos de UI.

CAPA
Escolha automaticamente o ambiente de acordo com a categoria predominante dos produtos.
Na capa: texto central grande, serifado, branco e em CAIXA ALTA; título curto e forte relacionado ao ambiente/tema; abaixo do título, faixa arredondada bege clara com texto marrom "com achadinhos da Shopee que ninguém conhece 👌"; pequenos corações de traço branco e brilhinhos; rodapé com "@raposacacadora" em pílula branca; iluminação quente; sem ícones de interface.

SLIDES DOS PRODUTOS
Para CADA produto, crie uma cena própria e fotorealista em ambiente coerente com sua utilização. Preserve fielmente o produto fornecido.
Na parte inferior: nome do produto em serifada elegante, texto centralizado e gradiente branco suave para legibilidade. NÃO usar watermark nos slides dos produtos.

REGRAS DE LEGENDA — PADRÃO OBRIGATÓRIO DA RAPOSA CAÇADORA
A legenda NÃO deve ser uma descrição genérica e NÃO deve listar URLs.

1. HOOK: primeira frase curta, natural e chamativa, específica para a categoria/tema.
2. CONTEXTO: 1 ou 2 frases curtas explicando por que os achadinhos são interessantes, úteis, bonitos ou versáteis.
3. LISTA DOS PRODUTOS: apresentar EXATAMENTE os {len(produtos_publicos)} produtos em lista numerada usando 1️⃣, 2️⃣, 3️⃣, 4️⃣, 5️⃣ etc., conforme a quantidade real. Use os nomes reais fornecidos.
4. CTA OBRIGATÓRIO, preservando esta ideia e linguagem:
"Tudo que você tá vendo aqui tá com LINK NA BIO e nos STORIES! 👉 Curte se você amou. 👉 Segue @raposacacadora pra não perder nenhum achado. 👉 Comenta \"EU QUERO\" que te mando todos os links no direct."
Pode ajustar pequenas palavras para a categoria, mas NÃO remover essas três ações.
5. PERGUNTA FINAL OBRIGATÓRIA:
"Qual desses {len(produtos_publicos)} é o seu favorito? 1, 2, 3, ...? 👇" Adaptar a numeração à quantidade real.
6. HASHTAGS DINÂMICAS: aproximadamente 5 a 10 hashtags relevantes para categoria, tema, produtos e estilo. Quando pertinente, incluir #achadosshopee, #shopee e #raposacacadora.

REGRAS IMPORTANTES DA LEGENDA
- NÃO colocar URLs brutas da Shopee dentro da legenda.
- NÃO escrever "Links dos achadinhos:" seguido de URLs.
- Os links de afiliado permanecem associados aos respectivos produtos no conteúdo estruturado e serão tratados pelo sistema fora da legenda.
- Não inventar características, preços, descontos, avaliações ou informações não fornecidas.
- A legenda deve soar humana, espontânea e específica para a categoria, evitando texto robótico ou repetitivo.
- Usar emojis com moderação.
- A legenda DEVE conter lista numerada, CTA, pergunta final e hashtags.

REGRAS DE CONTEÚDO
- Use somente os dados fornecidos.
- Preserve exatamente os links de afiliado recebidos.
- Gere a legenda seguindo OBRIGATORIAMENTE o padrão acima.

Retorne no structured output:
1. categoria identificada;
2. legenda completa, pronta para publicação e seguindo o padrão obrigatório;
3. ordem dos slides;
4. para cada slide, headline, benefício e asset_url/identificador do arquivo gerado.

ID interno do lote: {post_id}

DADOS DOS PRODUTOS:
{json.dumps(produtos_publicos, ensure_ascii=False, indent=2)}
""".strip()
    payload = {
        "message": {"content": [{"type": "text", "text": prompt, "visibility": "visible"}]},
        "title": f"Carrossel Instagram - lote {post_id}",
        "hide_in_task_list": True,
        "structured_output_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string"},
                "caption": {"type": "string"},
                "slides": {"type": "array", "items": {"type": "object", "properties": {"position": {"type": "integer"}, "product_id": {"type": "integer"}, "headline": {"type": "string"}, "benefit": {"type": "string"}, "asset_url": {"type": "string"}}, "required": ["position", "product_id", "headline", "benefit", "asset_url"], "additionalProperties": False}},
            },
            "required": ["category", "caption", "slides"],
            "additionalProperties": False,
        },
    }
    response = requests.post(f"{MANUS_API_URL}/v2/task.create", headers=_headers(), json=payload, timeout=60)
    try:
        data = response.json()
    except ValueError as exc:
        raise ManusAPIError(f"Resposta inválida da Manus (HTTP {response.status_code}).") from exc
    if response.status_code >= 400 or not data.get("ok", True):
        raise ManusAPIError(f"Erro ao criar tarefa Manus: {data}")
    return data

def enviar_mensagem_tarefa(task_id: str, content: str) -> dict[str, Any]:
    if not task_id:
        raise ManusAPIError("task_id ausente.")
    if not content.strip():
        raise ManusAPIError("Mensagem Manus vazia.")
    response = requests.post(f"{MANUS_API_URL}/v2/task.sendMessage", headers=_headers(), json={"task_id": task_id, "message": {"content": content.strip()}}, timeout=60)
    try:
        data = response.json()
    except ValueError as exc:
        raise ManusAPIError(f"Resposta inválida da Manus ao enviar mensagem (HTTP {response.status_code}).") from exc
    if response.status_code >= 400 or not data.get("ok", True):
        raise ManusAPIError(f"Erro ao enviar mensagem para a tarefa Manus: {data}")
    return data

def listar_mensagens_tarefa(task_id: str) -> dict[str, Any]:
    response = requests.get(f"{MANUS_API_URL}/v2/task.listMessages", headers=_headers(), params={"task_id": task_id, "order": "desc", "limit": 20}, timeout=60)
    try:
        data = response.json()
    except ValueError as exc:
        raise ManusAPIError(f"Resposta inválida da Manus (HTTP {response.status_code}).") from exc
    if response.status_code >= 400 or not data.get("ok", True):
        raise ManusAPIError(f"Erro ao consultar mensagens Manus: {data}")
    return data
