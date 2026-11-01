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
    pub fn log(&self, ts_ms: i64, ev: &JEvent) {
        if let Err(e) = self.ensure(ts_ms) {
            tracing::error!(error = %e, "journal: ouverture impossible");
            return;
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
            if let Err(e) = writeln!(f, "{line}").and_then(|_| f.flush()) {
                tracing::error!(error = %e, "journal: écriture impossible");
            }
        }
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
}
