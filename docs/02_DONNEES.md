# 02 — Données

## 1. Source : Dukascopy `.bi5`

Flux public `datafeed.dukascopy.com` — ticks réels top-of-book, résolution
milliseconde, format binaire 20 octets big-endian par tick :

```
struct REC {                # ">iiiff"
    int32  ms_off;          # offset ms dans l'heure du fichier
    float  ask;
    float  bid;
    float  ask_vol;
    float  bid_vol;
}
```

Un fichier `.bi5` par symbole et par heure. Le downloader (`fetch_poc.py`)
décode directement en Python (pas de dukascopy-node sur le POC), valide et
réécrit en CSV horaire :

```
/opt/arb/data/<SYM>/<YYYY-MM-DD>/<HH>.csv
ms_off,ask,bid,ask_vol,bid_vol
```

## 2. Univers ingéré

| Groupe | Symboles | Couverture POC (3 jours : 22/23/26 oct. 2026) |
|---|---|---|
| FX (J.2) | 28 paires | **28/28** — 62-68 h/72, trous intraday résiduels |
| Bouts (J.3) | 32 instruments | collectés en fond — 26/32 à date, inactifs sous Convention A |
| Exclus (J.4) | WHEAT, CORN, ES35, CHINA50, XNGUSD, HK50, SA40, feuilles, CME | jamais téléchargés |

Horodatage : `ts = EPOCH(day) + h×3 600 000 + ms_off` (UTC).

## 3. Contrôles qualité à l'ingestion

Compteurs par fichier, journalisés au ledger (`poc_ledger.json`) :

| Contrôle | Champ |
|---|---|
| `bad_px` | bid > ask (croisé) ou prix ≤ 0 → rejet |
| `bad_seq` | `ms_off` non monotone → rejet |
| `bad_ms` | offset hors [0, 3 600 000) → rejet |
| `bad_vol` | volumes négatifs → rejet |
| diviseur | auto-détecté parmi (1,10,…,10⁵) vs niveau de référence `UNIVERSE` |

La remise en file (≤2 tentatives) couvre les trous ; les passes finales du
ledger retentent les heures manquantes — AUDCHF 3 h → 67 h, GBPJPY 2 h → 67 h.

## 4. Discipline réseau (upstream = tarpit)

- **1 requête/s, un seul processus global** — toute concurrence a produit
  429 puis un blocage WAF 403 durable.
- 503 → backoff 30/90/300/600 s ; 403/429/WAF → **pause 2 h**, ne jamais
  marteler ; timeout 60 s (réponses ~18 s observées).
- Les gaps de couverture restants sont documentés — un trou de données se
  traduit en « pas de quote fraîche » → chemins invalidés, jamais fabriqués.

## 5. Volumes — sémantique par classe

`ask_vol`/`bid_vol` sont collectés et validés ; **non exploités par le POC**
(exec_seq ne borne pas le fill au volume affiché — écart documenté, garde
volume spécifiée dans le cœur Rust).

Unités mesurées (convention Dukascopy ≈ 10⁻⁶ de l'unité instrument) :

| Instrument | ask_vol médian | ≈ top-of-book |
|---|---|---|
| FX | ~1-3 | 1-3 M devise de base |
| AUSIDXAUD | 0,00024 | ~240 contrats (~2 M AUD) |
| CHEIDXCHF | 0,00012 | ~120 contrats (~1,7 M CHF) |
| BRENTCMDUSD | 0,00005 | ~54 contrats |
| COCOACMDUSD | 0,00006 | ~60 unités |

→ un ordre de 100 K$ tient dans le top-of-book FX quasi toujours, finement
sur les softs CFD — d'où la garde volume à spécifier.

## 6. Conversions de cotation (Annexe J.3)

| Instrument | Convention Dukas | Normalisation |
|---|---|---|
| `*CMDUSX` (COFFEE, COTTON, SOYBEAN, OJUICE) | cents US | bid/ask ÷ 100 |
| `CHEIDXCHF` (SWI20) | points CHF | nœud USD + coût conversion implicite `CONV_COST = 4 $` |
| Indices (DEU, ITA, FRA, EUS, NLD, GBR, JPN, AUS, USA*, CHE) | points indice, devise de cotation | devise → nœud graphe `BOUTS` |
| Métaux (XAU*, XAG*, XPT, XPD) | prix par once, devise de cotation | idem |
| Énergies/softs (LIGHT, BRENT, SUGAR, COCOA, COFFEE, COTTON, SOYBEAN, OJUICE) | prix par unité, USD | idem |

Table de référence : `poc_engine.BOUTS` (34 mappings duka → devise nœud).
Référence BRENT obsolète notée (ref 65 vs réel ~115) — l'auto-détection du
diviseur atterrit juste quand même ; table `UNIVERSE` à rafraîchir.

## 7. Artefacts connus

- **Épisode de ~51 h** : forward-fill à travers le week-end (ven→lun) —
  filtré au rapport (`durs > 3 600 s` = artefact), jamais interprété comme
  persistance réelle.
- **Sessions CFD** : cassures quotidiennes normales (indices EUR ~06-19 h UTC)
  — couvertes par la garde de fraîcheur, pas par du remplissage.
