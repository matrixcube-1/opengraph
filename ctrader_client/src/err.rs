//! Erreurs typées du client + actions recommandées par code d'erreur OA.
//!
//! `errorCode` est une **chaîne** dans les protos réels (ex.
//! `"NOT_ENOUGH_MONEY"`), contrairement au tableau d'entiers du guide.

use thiserror::Error;

#[derive(Debug, Error)]
pub enum ClientError {
    #[error("io: {0}")]
    Io(#[from] std::io::Error),
    #[error("protobuf: {0}")]
    Proto(String),
    #[error("trame trop longue: {0} octets")]
    FrameTooLong(usize),
    #[error("trame vide")]
    EmptyFrame,
    #[error("timeout {0}")]
    Timeout(&'static str),
    #[error("erreur venue {code}: {desc}")]
    Venue { code: String, desc: String },
    #[error("compte non NETTED (account_type={0:?}) — refus de trader")]
    NotNetted(String),
    #[error("symbole requis absent de la liste venue: {0}")]
    MissingSymbol(&'static str),
    #[error("connexion morte: {0}")]
    Dead(String),
    #[error("session terminée — arrêt demandé ({0})")]
    Shutdown(String),
    #[error("état incohérent: {0}")]
    Inconsistent(String),
}

/// Action recommandée face à un code d'erreur métier OA.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrAction {
    /// Rafraîchir le token (2173) puis re-account_auth.
    RefreshToken,
    /// Re-account_auth immédiat.
    ReAuth,
    /// Ralentir (REQUEST_FREQUENCY_EXCEEDED) : doubler l'intervalle mini.
    Throttle,
    /// Re-tenter après backoff (connexions limites).
    Retry,
    /// Attendre des quotes ; ne pas envoyer d'ordre.
    WaitQuotes,
    /// Réduire le volume de 50 % et alerter.
    HalveVolume,
    /// Stop nouveaux ordres + clôturer positions (exposition max).
    CloseOnly,
    /// Arrêt trading (marché fermé / compte suspendu / non autorisé).
    Halt,
    /// Abandonner cette opportunité (volume/stops invalides, short non permis).
    SkipOpportunity,
    /// Rapprocher l'état (un cancel impossible, divergence).
    Reconcile,
    /// Réponse inattendue hors catalogue — journaliser et continuer.
    Log,
}

/// Cartographie `errorCode` (chaîne) -> action. Non exhaustif : tout code
/// inconnu retombe sur `Log` (jamais de panique sur un code nouveau).
pub fn err_action(code: &str) -> ErrAction {
    match code {
        "OA_AUTH_TOKEN_EXPIRED" => ErrAction::RefreshToken,
        "ACCOUNT_NOT_AUTHORIZED" | "CH_CLIENT_NOT_AUTHENTICATED" => ErrAction::ReAuth,
        "REQUEST_FREQUENCY_EXCEEDED" => ErrAction::Throttle,
        "CONNECTIONS_LIMIT_EXCEEDED" | "BLOCKED_PAYLOAD_TYPE" | "CANT_ROUTE_REQUEST" => {
            ErrAction::Retry
        }
        "NO_QUOTES" | "MARKET_CLOSED" => ErrAction::WaitQuotes,
        "NOT_ENOUGH_MONEY" => ErrAction::HalveVolume,
        "MAX_EXPOSURE_REACHED" | "MIN_POSITION_VOLUME_REACHED" => ErrAction::CloseOnly,
        "TRADING_DISABLED" | "NO_TRADING" | "NO_LOGIN" | "PROTECTION_TOO_LATE"
        | "MARGIN_CALL_TRIGGERED" => ErrAction::Halt,
        "TRADING_BAD_VOLUME" | "TRADING_BAD_STOPS" | "SHORT_SELLING_NOT_ALLOWED"
        | "SYMBOL_NOT_FOUND" | "ENTITY_NOT_FOUND" | "INVALID_REQUEST" => {
            ErrAction::SkipOpportunity
        }
        "UNABLE_TO_CANCEL_ORDER" | "PENDING_EXECUTION" | "CONCURRENT_MODIFICATION" => {
            ErrAction::Reconcile
        }
        _ => ErrAction::Log,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn error_code_mapping_critical() {
        assert_eq!(err_action("OA_AUTH_TOKEN_EXPIRED"), ErrAction::RefreshToken);
        assert_eq!(err_action("ACCOUNT_NOT_AUTHORIZED"), ErrAction::ReAuth);
        assert_eq!(
            err_action("REQUEST_FREQUENCY_EXCEEDED"),
            ErrAction::Throttle
        );
        assert_eq!(err_action("NO_QUOTES"), ErrAction::WaitQuotes);
        assert_eq!(err_action("NOT_ENOUGH_MONEY"), ErrAction::HalveVolume);
        assert_eq!(err_action("MAX_EXPOSURE_REACHED"), ErrAction::CloseOnly);
        assert_eq!(err_action("TRADING_DISABLED"), ErrAction::Halt);
        assert_eq!(err_action("TRADING_BAD_VOLUME"), ErrAction::SkipOpportunity);
        assert_eq!(err_action("UNABLE_TO_CANCEL_ORDER"), ErrAction::Reconcile);
    }

    #[test]
    fn unknown_code_is_log_not_panic() {
        assert_eq!(err_action("FUTURE_CODE_XYZ"), ErrAction::Log);
        assert_eq!(err_action(""), ErrAction::Log);
    }
}
