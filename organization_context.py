"""Contexto de organização da Fase 2.

Esta camada cria a identidade persistente do operador atual sem substituir
a autenticação Telegram existente. A aplicação continuará usando o fluxo
legado até as fases posteriores adicionarem enforcement por tenant.
"""

from __future__ import annotations

from typing import Any


LEGACY_ORGANIZATION_SLUG = "raposa-cacadora-legado"


def bootstrap_legacy_context(
    supabase: Any,
    telegram_user_id: int,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
) -> dict[str, str]:
    """Cria/atualiza o usuário legado e sua associação à organização.

    Retorna IDs persistentes para que as próximas fases possam introduzir
    OrganizationContext sem precisar alterar o fluxo operacional de uma vez.
    """

    if supabase is None:
        raise RuntimeError("Supabase não inicializado.")

    organization_response = (
        supabase
        .table("organizations")
        .select("id,name,slug,status")
        .eq("slug", LEGACY_ORGANIZATION_SLUG)
        .limit(1)
        .execute()
    )

    if not organization_response.data:
        raise RuntimeError("Organização legada não encontrada.")

    organization = organization_response.data[0]
    if organization.get("status") != "active":
        raise RuntimeError("Organização legada não está ativa.")

    user_response = (
        supabase
        .table("saas_users")
        .upsert(
            {
                "telegram_user_id": int(telegram_user_id),
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
            },
            on_conflict="telegram_user_id",
        )
        .execute()
    )

    if not user_response.data:
        raise RuntimeError("Não foi possível registrar o usuário SaaS.")

    user = user_response.data[0]

    member_response = (
        supabase
        .table("organization_members")
        .upsert(
            {
                "organization_id": organization["id"],
                "user_id": user["id"],
                "role": "owner",
            },
            on_conflict="organization_id,user_id",
        )
        .execute()
    )

    if not member_response.data:
        raise RuntimeError("Não foi possível registrar o membro da organização.")

    return {
        "organization_id": str(organization["id"]),
        "user_id": str(user["id"]),
    }
