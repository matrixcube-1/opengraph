//! Journal d'audit JSONL — une ligne par événement, append + fsync léger.
//!
//! Traçabilité requise : quotes de décision, ordres envoyés, acks, fills,
//! partiels, rejets, aborts, résiduels, unwinds, settle, erreurs venue.
//! Le fichier est la source de vérité pour la réconciliation post-mortem.

use std::io::Write;
use std::path::PathBuf;
use std::sync::Mutex;

use serde::Serialize;

use crate::err::ClientError;

/// Événements journalisés (tag `ev` sur chaque ligne).
#[derive(Debug, Serialize)]
#[serde(tag = "ev", rename_all = "snake_case")]
pub enum JEvent {
    /// Opportunité détectée (eval_at) — toujours journalisée, mode detect
    /// inclus.
    Opportunity {
        t0: i64,
        pi: usize,
        net_usd: f64,
        ddd_usd: f64,
        start: u8,
        legs: u8,
        traded: bool,
    },
    /// Stamp horloge venue d'un ExecutionEvent 2126 — attribution de la
    /// latence : deltas entre `o_ts`/`d_ts` (même horloge serveur) et
    /// `rx_ms` (horloge locale à la réception).
    ExecStamp {
        coid: String,
        etype: &'static str,
        /// `order.utcLastUpdateTimestamp` venue (0 si absent).
        o_ts: i64,
        /// `deal.executionTimestamp` venue (0 si absent).
        d_ts: i64,
        rx_ms: i64,
        /// File interne socket->stratégie pour cet event précis.
        q_ms: i64,
    },
    /// Ordre jambe envoyé.
    LegSend {
        t0: i64,
        pi: usize,
        li: usize,
        edge: u8,
        side: &'static str,
        limit_px: f64,
        volume: i64,
        acc: f64,
        coid: String,
    },
    /// Résolution d'une jambe (filled/partiel).
    LegFill {
        t0: i64,
        pi: usize,
        li: usize,
        edge: u8,
        qty_base: f64,
        px_fill: f64,
        frac_eff: f64,
        px_node: f64,
        order_id: i64,
        lat_ms: i64,
        /// Délai file interne : arrivée socket -> début du traitement de
        /// l'event par la stratégie (isole local vs venue dans `lat_ms`).
        q_ms: i64,
        /// Commission venue débitée sur les fills (signée, devise du compte).
        commission: f64,
    },
    /// Jambe non remplie (cancel/reject/timeout) -> abort.
    LegAbort {
        t0: i64,
        pi: usize,
        li: usize,
        edge: u8,
        reason: String,
    },
    /// Ordre unwind envoyé.
    UnwindSend {
        t0: i64,
        edge: u8,
        side: &'static str,
        qty_base: f64,
        limit_px: f64,
        coid: String,
    },
    /// Résultat d'un ordre unwind.
    UnwindFill {
        t0: i64,
        edge: u8,
        qty_base: f64,
        px_fill: f64,
        order_id: i64,
        /// Délai file interne : arrivée socket -> traitement (cf. LegFill).
        q_ms: i64,
        /// Commission venue débitée (signée).
        commission: f64,
    },
    /// Bouclage modèle exec_settle (PnL sim + résiduel marqué).
    Settle {
        t0: i64,
        pi: usize,
        pnl_usd: f64,
        filled_legs: u8,
        partial_legs: u8,
        resid_usd: f64,
        aborted: bool,
        dur_ms: i64,
    },
    /// Résiduel réel laissé ouvert après Done (Convention A).
    Residual {
        t0: i64,
        pi: usize,
        pos: Vec<(u8, f64)>,
        resid_usd: f64,
    },
    /// Rapprochement venue vs positions internes — les deux côtés sont
    /// indexés par `symbolId` venue (comparable directement).
    Reconcile {
        venue: Vec<(i64, f64)>,
        internal: Vec<(i64, f64)>,
        divergent: bool,
    },
    /// Erreur venue (ExecutionEvent errorCode / OrderErrorEvent / ErrorRes).
    VenueError {
        code: String,
        desc: String,
        action: String,
    },
    /// Événement système (connexion, auth, session, watchdog, flatten).
    System { msg: String },
    /// Quote reconstruite (bid/ask forward-fillés) telle qu'ingérée par le
    /// moteur — flux haute fréquence dans un journal `ticks/` séparé :
    /// matière première de la calibration sur données venue réelles.
    /// `e` = indice d'arête FX (0..27, ordre `universe::FX`).
    Tick { e: u8, bid: f64, ask: f64 },
}

/// Journal append-only : `journal/YYYY-MM-DD.jsonl`.
pub struct Journal {
    dir: PathBuf,
    file: Mutex<Option<std::io::BufWriter<std::fs::File>>>,
    day: Mutex<i64>,
}

impl Journal {
    pub fn new(dir: &str) -> Result<Self, ClientError> {
        std::fs::create_dir_all(dir)?;
        Ok(Self {
            dir: PathBuf::from(dir),
            file: Mutex::new(None),
            day: Mutex::new(-1),
        })
    }

    /// Chemin du fichier du jour `ts_ms` (UTC).
    fn path_for(&self, ts_ms: i64) -> PathBuf {
        let day = ts_ms / 86_400_000;
        let d = chrono::DateTime::from_timestamp(day * 86_400, 0)
            .map(|t| t.format("%Y-%m-%d").to_string())
            .unwrap_or_else(|| "unknown".into());
        self.dir.join(format!("{d}.jsonl"))
    }

    /// Ouvre (ou roule) le fichier du jour.
    fn ensure(&self, ts_ms: i64) -> Result<(), ClientError> {
        let day = ts_ms / 86_400_000;
        let mut g = self.day.lock().unwrap();
        if *g == day {
            return Ok(());
        }
        let path = self.path_for(ts_ms);
        let f = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)?;
        *self.file.lock().unwrap() = Some(std::io::BufWriter::new(f));
        *g = day;
        Ok(())
    }

    /// Append une ligne JSON `{"ts":ms,"ev":...}` — flush à chaque écriture
    /// (le journal perd sa valeur si le process crashe entre les fills).
    /// Retourne les octets écrits (0 si échec) — sert au plafond TickLog.
    pub fn log(&self, ts_ms: i64, ev: &JEvent) -> usize {
        if let Err(e) = self.ensure(ts_ms) {
            tracing::error!(error = %e, "journal: ouverture impossible");
            return 0;
        }
        // serde interdit `flatten` + tag interne : on sérialise l'enum
        // en Value puis on insère "ts" dans l'objet racine.
        let line = match serde_json::to_value(ev) {
            Ok(serde_json::Value::Object(mut m)) => {
                m.insert("ts".into(), serde_json::Value::from(ts_ms));
                serde_json::Value::Object(m).to_string()
            }
            _ => "{\"ev\":\"ser_error\"}".into(),
        };
        let mut g = self.file.lock().unwrap();
        if let Some(f) = g.as_mut() {
            match writeln!(f, "{line}").and_then(|_| f.flush()) {
                Ok(()) => return line.len() + 1,
                Err(e) => tracing::error!(error = %e, "journal: écriture impossible"),
            }
        }
        0
    }
}

/// Journal de ticks plafonné — arrêt d'écriture (latched) au-delà de `cap`
/// octets cumulés, fichiers préexistants au démarrage inclus. Protection
/// disque de la capture venue ; le journal métier n'est JAMAIS plafonné.
pub struct TickLog {
    j: Journal,
    cap: u64,
    written: Mutex<u64>,
    stopped: Mutex<bool>,
}

impl TickLog {
    /// `cap` en octets, couvre l'existant du dossier + les écritures à venir.
    pub fn new(dir: &str, cap: u64) -> Result<Self, ClientError> {
        let j = Journal::new(dir)?;
        // fs::metadata(path) — pas DirEntry::metadata : sur Windows la taille
        // du répertoire est parfois en cache juste après une écriture.
        let existing: u64 = std::fs::read_dir(dir)
            .map(|rd| {
                rd.flatten()
                    .map(|e| std::fs::metadata(e.path()).map(|m| m.len()).unwrap_or(0))
                    .sum()
            })
            .unwrap_or(0);
        Ok(Self {
            j,
            cap,
            stopped: Mutex::new(existing >= cap),
            written: Mutex::new(existing),
        })
    }

    /// Écrit un tick ; `false` quand le plafond est atteint (définitif).
    pub fn log(&self, ts_ms: i64, e: u8, bid: f64, ask: f64) -> bool {
        if *self.stopped.lock().unwrap() {
            return false;
        }
        let n = self.j.log(ts_ms, &JEvent::Tick { e, bid, ask }) as u64;
        if n == 0 {
            return true; // échec d'écriture : on réessaiera, sans latch
        }
        let mut w = self.written.lock().unwrap();
        *w += n;
        if *w >= self.cap {
            *self.stopped.lock().unwrap() = true;
        }
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn writes_jsonl_and_rotates() {
        let dir = std::env::temp_dir().join(format!("og_jnl_{}", std::process::id()));
        let j = Journal::new(dir.to_str().unwrap()).unwrap();
        j.log(
            1_793_016_000_000, // 2026-10-26 12:00 UTC
            &JEvent::System { msg: "boot".into() },
        );
        j.log(
            1_793_016_000_000, // 2026-10-26 12:00 UTC
            &JEvent::Opportunity {
                t0: 1,
                pi: 42,
                net_usd: 3.25,
                ddd_usd: 9.1,
                start: 5,
                legs: 2,
                traded: false,
            },
        );
        // jour suivant -> autre fichier
        j.log(
            1_793_016_000_000 + 86_400_000,
            &JEvent::System { msg: "next day".into() },
        );
        let f1 = dir.join("2026-10-26.jsonl");
        let f2 = dir.join("2026-10-27.jsonl");
        let l1 = std::fs::read_to_string(&f1).unwrap();
        let l2 = std::fs::read_to_string(&f2).unwrap();
        assert!(l1.contains("\"ev\":\"system\"") && l1.contains("boot"));
        assert!(l1.contains("\"ev\":\"opportunity\"") && l1.contains("\"net_usd\":3.25"));
        assert!(l2.contains("next day"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// Le TickLog respecte le plafond disque : arrêt d'écriture au-delà
    /// du cap (fichiers existants au démarrage compris), état latched.
    #[test]
    fn tick_log_respects_cap() {
        let dir = std::env::temp_dir().join(format!("og_tickcap_{}", std::process::id()));
        let tl = TickLog::new(dir.to_str().unwrap(), 200).unwrap();
        let mut wrote = 0u32;
        for _ in 0..20 {
            if tl.log(1_793_016_000_123, 0, 1.0830, 1.0834) {
                wrote += 1;
            }
        }
        assert!(wrote >= 1 && wrote < 20, "wrote={wrote}");
        let total: u64 = std::fs::read_dir(&dir)
            .unwrap()
            .flatten()
            .map(|e| e.metadata().map(|m| m.len()).unwrap_or(0))
            .sum();
        assert!(total <= 200 + 96, "total={total} depasse cap+marge");
        // restart : l'existant est compté -> toujours stoppé
        let tl2 = TickLog::new(dir.to_str().unwrap(), 200).unwrap();
        assert!(!tl2.log(1_793_016_000_123, 0, 1.0, 1.1));
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// Les ticks capturés alimentent la calibration sur flux venue :
    /// la ligne doit porter edge + bid/ask reconstruits + ts (champ racine).
    #[test]
    fn tick_event_serializes_compact() {
        let dir = std::env::temp_dir().join(format!("og_jnl_tick_{}", std::process::id()));
        let j = Journal::new(dir.to_str().unwrap()).unwrap();
        j.log(
            1_793_016_000_123,
            &JEvent::Tick {
                e: 22,
                bid: 91.472,
                ask: 91.493,
            },
        );
        let f = dir.join("2026-10-26.jsonl");
        let l = std::fs::read_to_string(&f).unwrap();
        assert!(l.contains("\"ev\":\"tick\""), "ligne: {l}");
        assert!(l.contains("\"e\":22") && l.contains("\"bid\":91.472") && l.contains("\"ask\":91.493"));
        assert!(l.contains("\"ts\":1793016000123"));
        let _ = std::fs::remove_dir_all(&dir);
    }
}
