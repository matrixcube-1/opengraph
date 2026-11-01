//! Configuration — TOML + secrets par variables d'environnement.
//!
//! Jamais de secret dans le fichier ni les logs : `client_id`,
//! `client_secret`, `access_token` viennent de `CTRADER_*` (env).

use serde::Deserialize;

use crate::err::ClientError;

/// Environnement cTrader (hôtes séparés — jamais mixés).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Env {
    Demo,
    Live,
}

impl Env {
    pub fn host(&self) -> &'static str {
        match self {
            Env::Demo => "demo.ctraderapi.com",
            Env::Live => "live.ctraderapi.com",
        }
    }
    /// 5035 = protobuf (5036 = JSON, non utilisé).
    pub fn port(&self) -> u16 {
        5035
    }
}

/// Mode d'exécution de la stratégie.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum Mode {
    /// Journalise les opportunités, n'envoie JAMAIS d'ordre.
    #[default]
    Detect,
    /// Envoie les ordres sur le compte (démo ou live selon `env`).
    Trade,
}

/// Paramètres moteur figés (P) — mêmes valeurs que le run validé.
#[derive(Debug, Clone, Deserialize)]
pub struct EngineParams {
    #[serde(default = "d_fresh")]
    pub fresh_ms: f64,
    #[serde(default = "d_cap")]
    pub spread_cap: f64,
    #[serde(default = "d_comm")]
    pub comm_scale: f64,
    /// Seuil de déclenchement net USD (calibration trial #87 : 2.66 $).
    #[serde(default = "d_ddd")]
    pub ddd_min: f64,
    #[serde(default = "d_qmin")]
    pub q_min: f64,
    /// Timeout résolution jambe (ms) — IOC devrait être << 1 s.
    #[serde(default = "d_leg_to")]
    pub leg_timeout_ms: u64,
    /// Timeout requêtes auth/symbols (s).
    #[serde(default = "d_req_to")]
    pub req_timeout_s: u64,
}
fn d_fresh() -> f64 {
    909.0
}
fn d_cap() -> f64 {
    1.76
}
fn d_comm() -> f64 {
    1.10
}
fn d_ddd() -> f64 {
    2.66
}
fn d_qmin() -> f64 {
    0.5
}
fn d_leg_to() -> u64 {
    2_000
}
fn d_req_to() -> u64 {
    10
}

impl Default for EngineParams {
    fn default() -> Self {
        Self {
            fresh_ms: d_fresh(),
            spread_cap: d_cap(),
            comm_scale: d_comm(),
            ddd_min: d_ddd(),
            q_min: d_qmin(),
            leg_timeout_ms: d_leg_to(),
            req_timeout_s: d_req_to(),
        }
    }
}

/// Configuration complète d'un lancement.
#[derive(Debug, Deserialize)]
pub struct Config {
    pub env: Env,
    #[serde(default)]
    pub mode: Mode,
    /// `ctidTraderAccountId` du compte cible.
    pub ctid_trader_account_id: i64,
    /// Dossier des journaux JSONL (un fichier par jour).
    #[serde(default = "d_logdir")]
    pub journal_dir: String,
    /// Exiger un compte NETTED (garde-fou ProtoOATraderRes) — toujours true.
    #[serde(default = "d_true")]
    pub require_netted: bool,
    /// Intervalle reconcile positions (s).
    #[serde(default = "d_rec")]
    pub reconcile_s: u64,
    #[serde(default)]
    pub engine: EngineParams,
}
fn d_logdir() -> String {
    "journal".into()
}
fn d_true() -> bool {
    true
}
fn d_rec() -> u64 {
    30
}

/// Secrets — variables d'environnement obligatoires.
pub struct Secrets {
    pub client_id: String,
    pub client_secret: String,
    pub access_token: String,
    /// `refreshToken` OAuth (utilisé par 2173 si présent).
    pub refresh_token: Option<String>,
}

impl Secrets {
    pub fn from_env() -> Result<Self, ClientError> {
        let get = |k: &str| {
            std::env::var(k).map_err(|_| ClientError::Inconsistent(format!("env {k} manquant")))
        };
        Ok(Self {
            client_id: get("CTRADER_CLIENT_ID")?,
            client_secret: get("CTRADER_CLIENT_SECRET")?,
            access_token: get("CTRADER_ACCESS_TOKEN")?,
            refresh_token: std::env::var("CTRADER_REFRESH_TOKEN").ok(),
        })
    }
}

/// Charge + valide le TOML.
pub fn load(path: &str) -> Result<Config, ClientError> {
    let txt = std::fs::read_to_string(path)
        .map_err(|e| ClientError::Inconsistent(format!("config {path}: {e}")))?;
    let c: Config = toml::from_str(&txt)
        .map_err(|e| ClientError::Inconsistent(format!("config {path}: {e}")))?;
    if c.mode == Mode::Trade && c.ctid_trader_account_id == 0 {
        return Err(ClientError::Inconsistent("ctid_trader_account_id requis".into()));
    }
    Ok(c)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn endpoints_split_envs() {
        assert_eq!(Env::Demo.host(), "demo.ctraderapi.com");
        assert_eq!(Env::Live.host(), "live.ctraderapi.com");
        assert_eq!(Env::Demo.port(), 5035);
        assert_eq!(Env::Live.port(), 5035);
    }

    #[test]
    fn default_params_match_frozen_p() {
        let p = EngineParams::default();
        assert_eq!(p.fresh_ms, 909.0);
        assert_eq!(p.spread_cap, 1.76);
        assert_eq!(p.comm_scale, 1.10);
        assert_eq!(p.ddd_min, 2.66);
        assert_eq!(p.q_min, 0.5);
    }

    #[test]
    fn parses_toml() {
        let txt = r#"
            env = "demo"
            mode = "detect"
            ctid_trader_account_id = 12345678
            journal_dir = "/var/log/og"
            [engine]
            ddd_min = 3.0
        "#;
        let c: Config = toml::from_str(txt).unwrap();
        assert_eq!(c.env, Env::Demo);
        assert_eq!(c.mode, Mode::Detect);
        assert_eq!(c.ctid_trader_account_id, 12345678);
        assert_eq!(c.engine.ddd_min, 3.0);
        assert_eq!(c.engine.fresh_ms, 909.0); // défaut conservé
        assert!(c.require_netted);
    }

    #[test]
    fn trade_mode_requires_account() {
        let dir = std::env::temp_dir().join("og_cfg_test.toml");
        std::fs::write(
            &dir,
            "env=\"demo\"\nmode=\"trade\"\nctid_trader_account_id=0\n",
        )
        .unwrap();
        assert!(load(dir.to_str().unwrap()).is_err());
    }
}
