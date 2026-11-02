//! Market Data FIX 4.4 — souscription et parse des quotes top-of-book.
//!
//! - `subscribe_request` : 35=V, un MDReqID, 28 symboles FX + XAU/XAG
//!   (snapshot+subscribe 263=1, full book top-of-book 264=1).
//! - `Book` : etat par symbole — applique SnapshotFullRefresh (35=W) et
//!   IncrementalRefresh (35=X) ; groupes repetes 268=/279= parses par
//!   ordre d'apparition des tags (le groupe commence a 269/279 selon
//!   35 ; chaque entree = 269 type, 55 sym, 270 px, 271 qty [X] ou
//!   279 action+269+55+270+271 [W]).
//!
//! Entree type 269 : 0=Bid, 1=Offer. Action 279 (X) : 0=New,1=Change,
//! 2=Delete. On ne garde que le top-of-book (premier niveau par side) :
//! la strategie lit bid/ask executables, profondeur ignoree.

use std::collections::HashMap;
use crate::codec::FixMessage;

/// Symboles de l'univers (28 FX + 2 metaux en stand-by POC).
/// Convention venue a confirmer (EUR/USD vs EURUSD vs EUR/USD.FX).
pub const UNIVERSE: &[&str] = &[
    "EURUSD", "USDJPY", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF",
    "EURJPY", "GBPJPY", "AUDJPY", "NZDJPY", "CADJPY", "CHFJPY",
    "EURGBP", "EURAUD", "EURNZD", "EURCAD", "EURCHF",
    "GBPAUD", "GBPNZD", "GBPCAD", "GBPCHF",
    "AUDNZD", "AUDCAD", "AUDCHF", "NZDCAD", "NZDCHF", "CADCHF",
    "XAUUSD", "XAGUSD",
];

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Quote {
    pub bid: f64,
    pub ask: f64,
}

#[derive(Debug, Default)]
pub struct Book {
    /// bid/ask courants par symbole (Option separes : un cote peut
    /// manquer entre deux refreshes).
    bid: HashMap<String, f64>,
    ask: HashMap<String, f64>,
}

impl Book {
    pub fn new() -> Self { Self::default() }

    pub fn get(&self, sym: &str) -> Option<Quote> {
        match (self.bid.get(sym), self.ask.get(sym)) {
            (Some(&b), Some(&a)) => Some(Quote { bid: b, ask: a }),
            _ => None,
        }
    }

    /// Construit le MarketDataRequest (35=V) : snapshot+subscribe,
    /// top-of-book, types Bid+Offer, tous les symboles.
    /// `req_id` ex. "MD-1".
    pub fn subscribe_request(req_id: &str, symbols: &[&str]) -> Vec<(u32, String)> {
        let mut f = vec![
            (262, req_id.into()),              // MDReqID
            (263, "1".into()),                 // Snapshot + Subscribe
            (264, "1".into()),                 // MarketDepth = top of book
            (265, "0".into()),                 // MDUpdateType = full refresh
            (267, "2".into()),                 // NoMDEntryTypes
            (269, "0".into()),                 // Bid
            (269, "1".into()),                 // Offer
            (146, symbols.len().to_string()),  // NoRelatedSym
        ];
        for s in symbols {
            f.push((55, s.to_string()));
        }
        f
    }

    /// Applique un message MD entrant. Retourne les symboles dont la
    /// quote top-of-book a change (pour ingest moteur).
    pub fn apply(&mut self, m: &FixMessage) -> Vec<String> {
        let mut touched = Vec::new();
        match m.msg_type() {
            Some("W") => {
                // Snapshot : groupes 268=N puis par entree
                // 269 type, 55 sym, 270 px, 271 size.
                for g in groups(m, 268, 269) {
                    self.apply_entry(&g, None, &mut touched);
                }
            }
            Some("X") => {
                // Incremental : groupes 279=Action d'abord.
                for g in groups(m, 268, 279) {
                    self.apply_entry(&g, None, &mut touched);
                }
            }
            _ => {}
        }
        touched
    }

    fn apply_entry(&mut self, g: &[(u32, &str)], _ctx: Option<()>,
                   touched: &mut Vec<String>) {
        // Incremental : 279 action. Snapshot : pas de 279 (implicite New).
        let action = field(g, 279).unwrap_or("0");
        let etype = match field(g, 269) {
            Some(t) => t,
            None => return,
        };
        let sym = match field(g, 55) {
            Some(s) => s.to_string(),
            None => return,
        };
        let px: Option<f64> = field(g, 270).and_then(|s| s.parse().ok());
        let book_side = if etype == "0" {
            &mut self.bid
        } else if etype == "1" {
            &mut self.ask
        } else {
            return; // autres types (trade, etc.) ignores
        };
        match action {
            "2" => { book_side.remove(&sym); touched.push(sym); }
            _ => {
                if let Some(p) = px {
                    if book_side.get(&sym) != Some(&p) {
                        book_side.insert(sym.clone(), p);
                        touched.push(sym);
                    }
                }
            }
        }
    }
}

/// Reconstitue les groupes repetes : `count_tag` annonce N groupes ;
/// chaque groupe debute par `first_tag`. Retourne des tranches de
/// champs (tag,valeur) par groupe, dans l'ordre du message.
fn groups<'a>(m: &'a FixMessage, count_tag: u32, first_tag: u32)
    -> Vec<Vec<(u32, &'a str)>>
{
    let n: usize = match m.get(count_tag).and_then(|s| s.parse().ok()) {
        Some(n) => n,
        None => return Vec::new(),
    };
    let mut out: Vec<Vec<(u32, &str)>> = Vec::new();
    let mut seen_first = false;
    for (t, v) in &m.fields {
        if *t == count_tag {
            continue;
        }
        if *t == first_tag {
            out.push(Vec::new());
            seen_first = true;
        }
        if seen_first {
            if let Some(g) = out.last_mut() {
                g.push((*t, v.as_str()));
            }
        }
    }
    out.truncate(n);
    out
}

fn field<'a>(g: &'a [(u32, &'a str)], tag: u32) -> Option<&'a str> {
    g.iter().find(|(t, _)| *t == tag).map(|(_, v)| *v)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn md(msg_type: &str, body: &[(u32, &str)]) -> FixMessage {
        let mut f = vec![(35, msg_type.to_string())];
        for (t, v) in body {
            f.push((*t, v.to_string()));
        }
        FixMessage::new(f)
    }

    #[test]
    fn subscribe_request_shape() {
        let f = Book::subscribe_request("MD-1", &UNIVERSE[..3]);
        let m = FixMessage::new(f);
        assert_eq!(m.get(262), Some("MD-1"));
        assert_eq!(m.get(263), Some("1"));   // snapshot+subscribe
        assert_eq!(m.get(264), Some("1"));   // top of book
        assert_eq!(m.get(146), Some("3"));   // 3 symboles
    }

    #[test]
    fn snapshot_sets_top_of_book() {
        let mut b = Book::new();
        let w = md("W", &[
            (268, "2"),
            (269, "0"), (55, "EURUSD"), (270, "1.12050"), (271, "1000000"),
            (269, "1"), (55, "EURUSD"), (270, "1.12051"), (271, "1000000"),
        ]);
        let touched = b.apply(&w);
        assert_eq!(touched, vec!["EURUSD", "EURUSD"]);
        let q = b.get("EURUSD").unwrap();
        assert_eq!(q.bid, 1.12050);
        assert_eq!(q.ask, 1.12051);
    }

    #[test]
    fn incremental_change_and_delete() {
        let mut b = Book::new();
        b.apply(&md("W", &[
            (268, "2"),
            (269, "0"), (55, "CHFJPY"), (270, "169.100"), (271, "1"),
            (269, "1"), (55, "CHFJPY"), (270, "169.120"), (271, "1"),
        ]));
        // Change ask
        let x = md("X", &[
            (268, "1"),
            (279, "1"), (269, "1"), (55, "CHFJPY"), (270, "169.115"),
        ]);
        b.apply(&x);
        assert_eq!(b.get("CHFJPY").unwrap().ask, 169.115);
        // Delete bid => quote incomplete
        let x = md("X", &[
            (268, "1"),
            (279, "2"), (269, "0"), (55, "CHFJPY"),
        ]);
        b.apply(&x);
        assert!(b.get("CHFJPY").is_none());
    }

    #[test]
    fn multiple_symbols_one_incremental() {
        let mut b = Book::new();
        b.apply(&md("X", &[
            (268, "3"),
            (279, "0"), (269, "0"), (55, "EURUSD"), (270, "1.10"),
            (279, "0"), (269, "1"), (55, "EURUSD"), (270, "1.1001"),
            (279, "0"), (269, "0"), (55, "USDJPY"), (270, "150.50"),
        ]));
        assert_eq!(b.get("EURUSD").unwrap().bid, 1.10);
        assert!(b.get("USDJPY").is_some() == false); // pas d'ask encore
    }

    #[test]
    fn missing_group_fields_ignored() {
        let mut b = Book::new();
        // entree sans symbole ni type => ignoree, pas de panic
        b.apply(&md("X", &[
            (268, "2"),
            (279, "0"), (270, "1.23"),
            (279, "0"), (269, "0"), (55, "GBPUSD"), (270, "1.30"),
        ]));
        assert_eq!(b.bid.get("GBPUSD").copied(), Some(1.30));
    }
}
