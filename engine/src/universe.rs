//! Univers Annexe J.2 — 8 devises, 28 paires FX.
//! Les indices de devises suivent EXACTEMENT l'ordre de `CURR` du POC Python
//! (non alphabétique — ne pas réordonner, les tables en dépendent).

/// Devises (nœuds du graphe). Indice = `CI[ccy]` du moteur de référence.
pub const CURR: [&str; 8] = ["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"];

/// Les 28 paires FX (cotation marché base/quote), ordre Annexe J.
pub const FX: [&str; 28] = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD", "EURGBP", "EURJPY",
    "EURCHF", "EURAUD", "EURCAD", "EURNZD", "GBPJPY", "GBPCHF", "GBPAUD", "GBPCAD", "GBPNZD",
    "AUDJPY", "AUDCHF", "AUDCAD", "AUDNZD", "CADJPY", "CADCHF", "CHFJPY", "NZDJPY", "NZDCAD",
    "NZDCHF",
];

/// Indice de devise (panique si inconnue — l'univers est fermé).
pub fn ci(ccy: &str) -> u8 {
    CURR.iter()
        .position(|&c| c == ccy)
        .expect("devise hors univers") as u8
}

/// (base_idx, quote_idx) d'une paire FX par indice de table `FX`.
pub fn pair_nodes(edge: usize) -> (u8, u8) {
    let s = FX[edge];
    (ci(&s[0..3]), ci(&s[3..6]))
}

/// Indice d'arête `FX` de la paire USD d'une devise (`cUSD` ou `USDc`) ;
/// `None` pour USD elle-même (pas de paire USDUSD).
pub fn usd_edge(c: u8) -> Option<u8> {
    if c == 0 {
        return None;
    }
    (0..FX.len() as u8).find(|&e| {
        let (a, b) = pair_nodes(e as usize);
        (a == 0 && b == c) || (a == c && b == 0)
    })
}
