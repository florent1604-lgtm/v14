FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY . .
RUN pip install --no-cache-dir .

# ── Étage de test ───────────────────────────────────────────────────────────
#
# POURQUOI IL EXISTE
# Le poste Windows a Smart App Control actif (politique {0283AC0F-...}, livrée
# par Microsoft, immuable). Il refuse de charger toute extension native non
# signée que l'Intelligent Security Graph ne juge pas réputée. Le venv en
# contient 419, TOUTES non signées — numpy, pandas, scipy, sklearn comprises.
# Elles ne passent aujourd'hui que par réputation. Le 07/09/2026 `xxhash` l'a
# perdue et 30 fichiers de tests sont devenus incollectables d'un coup.
#
# Code Integrity ne s'applique pas aux binaires Linux : cet étage supprime la
# CLASSE entière du problème, pas seulement l'instance xxhash. Il aligne aussi
# les tests locaux sur la CI, qui tourne déjà sous Linux.
#
# Signer les 419 extensions ne marcherait pas : SAC ne consulte pas le magasin
# de certificats de la machine, contrairement à une politique WDAC d'entreprise.
#
#   docker build --target tests -t v14-tests .
#   docker run --rm v14-tests                      # suite complète
#   docker run --rm v14-tests pytest tests/test_riskgate.py -q
#   docker run --rm v14-tests ruff check titanium tools tests cli
#
# `results/` (29 Go) et `.venv/` sont exclus par `.dockerignore` : le contexte
# de build reste léger et l'image ne contient aucune donnée de marché.
FROM builder AS tests

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1

# `mt5` n'est pas installé : son marqueur `sys_platform == 'win32'` l'exclut
# d'office ici. Les tests qui exigent le terminal doivent donc se sauter
# proprement — si l'un d'eux échoue au lieu de sauter, c'est un défaut de test,
# pas un défaut d'environnement.
RUN pip install --no-cache-dir ".[dev]"

WORKDIR /build
CMD ["pytest", "tests/", "-q"]


# ── Image d'exécution ───────────────────────────────────────────────────────
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN useradd --create-home appuser \
 && install -d -m 0755 -o appuser -g appuser /home/appuser/.tradingagents
USER appuser
WORKDIR /home/appuser/app

COPY --from=builder --chown=appuser:appuser /build .

ENTRYPOINT ["tradingagents"]
