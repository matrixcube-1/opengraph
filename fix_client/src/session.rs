//! Session FIX 4.4 — machine d'etat pure (sans reseau) :
//! logon, numeros de sequence, heartbeat, TestRequest, ResendRequest,
//! SequenceReset (gap fill). Le transport (TCP/TLS) reste a venir —
//! cette couche ne sait que produire/consommer des `FixMessage`.
//!
//! Regles implementees (FIX 4.4 session layer) :
//! - Logon (35=A) sortant : 98=0 (aucun chiffrement mot de passe),
//!   108=HeartBtInt, 141=Y (ResetSeqNum au logon).
//! - SeqNum sortant incremente par message ; entrant attendu strictement
//!   croissant — gap => ResendRequest (35=2, 7=attendu, 16=0).
//! - TestRequest (35=1) => Heartbeat (35=0) + 112=TestReqID echo.
//! - SequenceReset-GapFill (35=4, 123=Y) => avance l'attendu a 36=N.
//! - MsgSeqNum entrant < attendu et != possdup => erreur fatale session.

use crate::codec::FixMessage;

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum State {
    /// Avant envoi du Logon.
    Fresh,
    /// Logon envoye, en attente du Logon venue.
    LogonSent,
    /// Session etablie (Logon venue recu).
    Active,
    /// Erreur fatale (seqnum bas non-dup, logout) — reconnexion requise.
    Dead,
}

#[derive(Debug)]
pub enum SessionError {
    /// SeqNum entrant inferieur a l'attendu sans PossDupFlag — protocole
    /// exige deconnexion immediate (message potentiellement perdu).
    SeqTooLow { got: u32, want: u32 },
    /// Champ obligatoire absent.
    MissingTag(u32),
    /// Logout recu (35=5).
    Logout(String),
}

pub struct Session {
    pub sender: String,
    pub target: String,
    pub hb_interval_s: u32,
    pub state: State,
    seq_out: u32,
    seq_in_expected: u32,
}

impl Session {
    pub fn new(sender: &str, target: &str, hb_interval_s: u32) -> Self {
        Self {
            sender: sender.into(),
            target: target.into(),
            hb_interval_s,
            state: State::Fresh,
            seq_out: 1,
            seq_in_expected: 1,
        }
    }

    /// Enveloppe session : ajoute 34/49/52/56, incremente seq_out.
    /// `now` = "YYYYMMDD-HH:MM:SS.mmm" UTC (tag 52).
    fn wrap(&mut self, msg_type: &str, mut body: Vec<(u32, String)>, now: &str)
        -> FixMessage
    {
        let mut f = Vec::with_capacity(body.len() + 5);
        f.push((35, msg_type.into()));
        f.push((34, self.seq_out.to_string()));
        f.push((49, self.sender.clone()));
        f.push((52, now.into()));
        f.push((56, self.target.clone()));
        f.append(&mut body);
        self.seq_out += 1;
        FixMessage::new(f)
    }

    /// Logon (35=A) : 98=0, 108=hb, 141=Y reset-seqnum.
    pub fn logon(&mut self, now: &str) -> FixMessage {
        self.state = State::LogonSent;
        self.wrap("A", vec![
            (98, "0".into()),
            (108, self.hb_interval_s.to_string()),
            (141, "Y".into()),
        ], now)
    }

    /// Heartbeat (35=0), 112 optionnel (echo TestReqID).
    pub fn heartbeat(&mut self, now: &str, test_req_id: Option<&str>)
        -> FixMessage
    {
        let mut b = Vec::new();
        if let Some(id) = test_req_id {
            b.push((112, id.into()));
        }
        self.wrap("0", b, now)
    }

    /// TestRequest (35=1) sortant (watchdog entrant).
    pub fn test_request(&mut self, now: &str, id: &str) -> FixMessage {
        self.wrap("1", vec![(112, id.into())], now)
    }

    /// Traite un message entrant ; retourne les reponses a envoyer.
    /// Verifie d'abord le SeqNum (tag 34) puis dispatche sur 35.
    pub fn on_message(&mut self, m: &FixMessage, now: &str)
        -> Result<Vec<FixMessage>, SessionError>
    {
        let seq: u32 = m.get(34)
            .and_then(|s| s.parse().ok())
            .ok_or(SessionError::MissingTag(34))?;
        let poss_dup = m.get(43) == Some("Y");
        if seq < self.seq_in_expected && !poss_dup {
            self.state = State::Dead;
            return Err(SessionError::SeqTooLow {
                got: seq, want: self.seq_in_expected,
            });
        }
        let expected = self.seq_in_expected;
        let gap = seq > expected;
        let mut out = Vec::new();
        match m.msg_type().unwrap_or("") {
            "A" => {
                // Logon venue => session active ; aligne l'attendu.
                self.seq_in_expected = seq + 1;
                self.state = State::Active;
            }
            "0" => { self.seq_in_expected = seq + 1; } // Heartbeat
            "1" => {
                // TestRequest => Heartbeat avec 112 echo.
                self.seq_in_expected = seq + 1;
                let id = m.get(112).unwrap_or("").to_string();
                out.push(self.heartbeat(now, Some(&id)));
            }
            "2" => {
                // ResendRequest (7=BeginSeqNo, 16=EndSeqNo) : nous ne
                // re-envoyons pas les anciens messages (journal local
                // suffit) => SequenceReset-GapFill (35=4, 123=Y,
                // 36=seq_out courant).
                self.seq_in_expected = seq + 1;
                let new_no = self.seq_out;
                let msg = self.wrap("4", vec![
                    (123, "Y".into()),
                    (36, new_no.to_string()),
                ], now);
                out.push(msg);
            }
            "4" => {
                // SequenceReset : si GapFill (123=Y), saute a 36=N.
                if m.get(123) == Some("Y") {
                    let new_no: u32 = m.get(36)
                        .and_then(|s| s.parse().ok())
                        .ok_or(SessionError::MissingTag(36))?;
                    self.seq_in_expected = new_no;
                } else {
                    self.seq_in_expected = seq + 1;
                }
            }
            "5" => {
                self.state = State::Dead;
                return Err(SessionError::Logout(
                    m.get(58).unwrap_or("").to_string()));
            }
            _ => {
                // Message applicatif ou autre : consomme le seqnum.
                self.seq_in_expected = seq + 1;
            }
        }
        // Gap detecte => ResendRequest pour la fourchette manquante :
        // 7=BeginSeqNo (attendu avant consommation), 16=0 (jusqu'a la fin).
        if gap {
            out.push(self.wrap("2", vec![
                (7, expected.to_string()),
                (16, "0".into()),
            ], now));
        }
        Ok(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn venue(msg_type: &str, seq: u32) -> FixMessage {
        FixMessage::new(vec![
            (35, msg_type.into()), (34, seq.to_string()),
            (49, "VENUE".into()), (56, "OG".into()),
        ])
    }

    #[test]
    fn logon_handshake_and_seqnums() {
        let mut s = Session::new("OG", "VENUE", 30);
        assert_eq!(s.state, State::Fresh);
        let lg = s.logon("20261102-19:00:00.000");
        assert_eq!(s.state, State::LogonSent);
        assert_eq!(lg.get(35), Some("A"));
        assert_eq!(lg.get(98), Some("0"));
        assert_eq!(lg.get(108), Some("30"));
        assert_eq!(lg.get(141), Some("Y"));
        assert_eq!(lg.get(34), Some("1")); // premier seqnum
        assert_eq!(lg.get(49), Some("OG"));
        assert_eq!(lg.get(56), Some("VENUE"));
        // venue logon => Active
        s.on_message(&venue("A", 1), "now").unwrap();
        assert_eq!(s.state, State::Active);
        // second message sortant : seqnum 2
        let hb = s.heartbeat("now", None);
        assert_eq!(hb.get(34), Some("2"));
    }

    #[test]
    fn test_request_echoed_in_heartbeat() {
        let mut s = Session::new("OG", "VENUE", 30);
        s.logon("t");
        s.on_message(&venue("A", 1), "t").unwrap();
        let mut tr = venue("1", 2);
        tr.fields.push((112, "REQ-42".into()));
        let resp = s.on_message(&tr, "t").unwrap();
        assert_eq!(resp.len(), 1);
        assert_eq!(resp[0].get(35), Some("0"));
        assert_eq!(resp[0].get(112), Some("REQ-42"));
    }

    #[test]
    fn seq_gap_triggers_resend_request() {
        let mut s = Session::new("OG", "VENUE", 30);
        s.logon("t");
        s.on_message(&venue("A", 1), "t").unwrap();
        // on attend 2, on recoit 4 => gap [2..4)
        let resp = s.on_message(&venue("0", 4), "t").unwrap();
        let rr = resp.iter().find(|m| m.get(35) == Some("2"));
        assert!(rr.is_some(), "ResendRequest attendu : {:?}", resp);
    }

    #[test]
    fn gapfill_sequence_reset_advances_expected() {
        let mut s = Session::new("OG", "VENUE", 30);
        s.logon("t");
        s.on_message(&venue("A", 1), "t").unwrap();
        let mut gf = venue("4", 2);
        gf.fields.push((123, "Y".into()));
        gf.fields.push((36, "10".into()));
        s.on_message(&gf, "t").unwrap();
        assert_eq!(s.seq_in_expected, 10);
        // prochain message a 10 : pas de gap
        let resp = s.on_message(&venue("0", 10), "t").unwrap();
        assert!(resp.iter().all(|m| m.get(35) != Some("2")));
    }

    #[test]
    fn seq_too_low_without_possdup_is_fatal() {
        let mut s = Session::new("OG", "VENUE", 30);
        s.logon("t");
        s.on_message(&venue("A", 1), "t").unwrap();
        s.on_message(&venue("0", 2), "t").unwrap();
        // re-recoit seq 2 sans PossDup => fatal
        match s.on_message(&venue("0", 2), "t") {
            Err(SessionError::SeqTooLow { got: 2, want: 3 }) => {}
            other => panic!("attendu SeqTooLow, eu {:?}", other),
        }
        assert_eq!(s.state, State::Dead);
        // avec PossDupFlag=Y : tolere
        let mut s2 = Session::new("OG", "VENUE", 30);
        s2.logon("t");
        s2.on_message(&venue("A", 1), "t").unwrap();
        s2.on_message(&venue("0", 2), "t").unwrap();
        let mut dup = venue("0", 2);
        dup.fields.push((43, "Y".into()));
        assert!(s2.on_message(&dup, "t").is_ok());
        assert_eq!(s2.state, State::Active);
    }

    #[test]
    fn logout_kills_session() {
        let mut s = Session::new("OG", "VENUE", 30);
        s.logon("t");
        s.on_message(&venue("A", 1), "t").unwrap();
        match s.on_message(&venue("5", 2), "t") {
            Err(SessionError::Logout(_)) => {}
            other => panic!("attendu Logout, eu {:?}", other),
        }
        assert_eq!(s.state, State::Dead);
    }
}
