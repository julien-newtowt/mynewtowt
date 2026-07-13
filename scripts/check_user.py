#!/usr/bin/env python3
"""Diagnostic de connexion d'un compte staff (« pourquoi X n'arrive pas à se connecter »).

Reproduit, pour un ``username`` donné, chaque garde du login staff
(``routers/staff_auth_router.login``) et pointe ce qui empêcherait la connexion.
Lecture seule : n'écrit rien, ne révèle ni le hash ni le secret MFA.

Gardes vérifiées, dans l'ordre du login réel :
  1. compte trouvé (par username exact — le login est sensible à la casse) ;
  2. compte actif (``is_active``) — sinon le login renvoie « Identifiants
     incorrects », message trompeur identique à un mauvais mot de passe ;
  3. hash de mot de passe bcrypt valide ;
  4. (option) le mot de passe saisi correspond ;
  5. MFA : activé ⇒ un code TOTP est exigé après le mot de passe ;
  6. ``must_change_password`` : connexion OK mais redirigée vers le changement ;
  7. rate-limit IP (indicateur) : tentatives récentes sur ``staff_login_ip``.

Usage (sur le serveur ; invocation via ``-m`` comme les autres scripts) :
  docker compose exec app python -m scripts.check_user <username>
  docker compose exec -it app python -m scripts.check_user <username> --check-password
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select

from app.auth import verify_password
from app.database import SessionLocal
from app.models.rate_limit import RateLimitAttempt
from app.models.user import User

# Couleurs seulement en terminal interactif (sortie propre si pipé/loggé).
_TTY = sys.stdout.isatty()
OK = "\033[0;32m✓\033[0m" if _TTY else "[OK]"
NO = "\033[0;31m✗\033[0m" if _TTY else "[X]"
WARN = "\033[0;33m•\033[0m" if _TTY else "[!]"


async def _find(session, username: str) -> tuple[User | None, str | None]:
    """Trouve l'utilisateur. Renvoie (user, note) où note signale une
    correspondance approchée (casse, email) utile au diagnostic."""
    u = await session.scalar(select(User).where(User.username == username))
    if u:
        return u, None
    # Correspondances approchées pour expliquer un échec « introuvable ».
    u = await session.scalar(
        select(User).where(
            or_(func.lower(User.username) == username.lower(), func.lower(User.email) == username.lower())
        )
    )
    if u and u.username.lower() == username.lower():
        return u, f"casse différente — utilisez exactement « {u.username} »"
    if u:
        return u, f"« {username} » est un email ; le login staff se fait par USERNAME « {u.username} »"
    return None, None


async def _run(username: str, check_password: bool) -> None:
    async with SessionLocal() as session:
        user, note = await _find(session, username)
        print(f"\n=== Diagnostic connexion staff : « {username} » ===")
        if user is None:
            print(f"{NO} Aucun compte pour ce username ni cet email.")
            print("   → vérifiez l'orthographe, ou créez le compte via /admin/users.")
            return
        if note:
            print(f"{WARN} {note}")

        print(f"\n  id={user.id}  role={user.role}  langue={user.language}")
        print(f"  email={user.email}")
        if user.full_name:
            print(f"  nom={user.full_name}")
        print(f"  navire de rattachement={user.assigned_vessel_id or '— (tous)'}")
        print(f"  créé le={user.created_at}  dernière connexion={user.last_login_at or 'jamais'}")

        print("\n  Gardes du login :")
        # 2. actif
        if user.is_active:
            print(f"  {OK} compte actif")
        else:
            print(f"  {NO} compte DÉSACTIVÉ → login refusé (message trompeur « Identifiants incorrects »).")
            print("       → réactivez-le : /admin/users (bouton Activer) ou toggle en base.")
        # 3. hash
        h = user.hashed_password or ""
        if h.startswith(("$2a$", "$2b$", "$2y$")):
            print(f"  {OK} mot de passe : hash bcrypt présent")
        else:
            print(f"  {NO} hash de mot de passe absent/non-bcrypt → réinitialisez : python -m scripts.reset_password {user.username}")
        # 4. mot de passe (option)
        if check_password:
            pw = getpass.getpass("       mot de passe à tester (saisie masquée) : ")
            if pw:
                ok = False
                try:
                    ok = verify_password(pw, h)
                except Exception:  # hash invalide
                    ok = False
                print(f"       {OK if ok else NO} le mot de passe saisi { 'correspond' if ok else 'NE correspond PAS' }")
        # 5. MFA
        if getattr(user, "mfa_enabled", False) and user.mfa_secret:
            print(f"  {WARN} MFA activé → un code TOTP est exigé après le mot de passe.")
            print("       → si l'app d'authentification est perdue : réinitialisez le MFA depuis /admin/users.")
        else:
            print(f"  {OK} MFA non requis")
        # 6. changement de mot de passe forcé
        if user.must_change_password:
            print(f"  {WARN} must_change_password = vrai → à la connexion, redirection FORCÉE vers")
            print("       /admin/my-account/change-password (normal ; ce n'est pas un blocage de login).")
        # 7. rate-limit (indicateur global du scope, par IP)
        since = datetime.now(UTC) - timedelta(minutes=10)
        recent = int(
            await session.scalar(
                select(func.count(RateLimitAttempt.id))
                .where(RateLimitAttempt.scope == "staff_login_ip")
                .where(RateLimitAttempt.attempted_at >= since)
            )
            or 0
        )
        if recent:
            print(f"  {WARN} {recent} tentative(s) de login échouée(s) (toutes IP) sur les 10 dernières min.")
            print("       Le blocage est PAR IP : ≥ 10 échecs/10 min sur une même IP → « Trop de tentatives ».")
        else:
            print(f"  {OK} aucune tentative de login échouée récente")

        # Synthèse orientée « marin/commandant »
        if user.role == "marins":
            print("\n  Note profil « marins » : la connexion aboutit sur /dashboard, mais l'espace")
            print("  commandant (/captain) est en CONSULTATION seule par défaut — pour agir")
            print("  (vente à bord, SOF…), activez « Modifier » sur (marins × captain) dans /admin/permissions.")
        print()


def main() -> None:
    p = argparse.ArgumentParser(description="Diagnostic de connexion d'un compte staff.")
    p.add_argument("username", help="username (ou email) du compte à diagnostiquer")
    p.add_argument(
        "--check-password",
        action="store_true",
        help="tester un mot de passe (saisie masquée ; nécessite -it sur docker compose exec)",
    )
    args = p.parse_args()
    asyncio.run(_run(args.username, args.check_password))


if __name__ == "__main__":
    main()
