//! Codec FIX 4.4 — messages `tag=value<SOH>` avec enveloppe
//! `8=FIX.4.4 | 9=<BodyLength> | <corps> | 10=<CheckSum>`.
//!
//! BodyLength = nombre d'octets entre le SOH terminant le tag 9 et le
//! premier octet du tag 10. CheckSum = somme de TOUS les octets du
//! message (SOH inclus) jusqu'au SOH avant 10, modulo 256, 3 chiffres
//! zero-padded. Le parser rejette : corps tronque, checksum faux,
//! BodyLength incoherent, tags dupliques, valeurs vides (sauf champs
//! binaires connus — hors scope).

use std::collections::BTreeMap;
use std::fmt;

pub const SOH: u8 = 0x01;
pub const BEGIN_STRING: &str = "FIX.4.4";

#[derive(Debug, Clone, PartialEq)]
pub struct FixMessage {
    /// Corps ordonne (tag, valeur) — sans 8/9/10 (enveloppe).
    pub fields: Vec<(u32, String)>,
}

#[derive(Debug)]
pub enum CodecError {
    Truncated,
    BadBeginString,
    BadBodyLength,
    BadCheckSum { got: u32, want: u32 },
    Malformed,
    DuplicateTag(u32),
    EmptyValue(u32),
}

impl fmt::Display for CodecError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Truncated => write!(f, "message tronque"),
            Self::BadBeginString => write!(f, "BeginString != FIX.4.4"),
            Self::BadBodyLength => write!(f, "BodyLength incoherent"),
            Self::BadCheckSum { got, want } => {
                write!(f, "CheckSum {got} != attendu {want}")
            }
            Self::Malformed => write!(f, "champ malforme"),
            Self::DuplicateTag(t) => write!(f, "tag {t} duplique"),
            Self::EmptyValue(t) => write!(f, "tag {t} valeur vide"),
        }
    }
}
impl std::error::Error for CodecError {}

fn checksum(bytes: &[u8]) -> u32 {
    bytes.iter().map(|&b| b as u32).sum::<u32>() % 256
}

impl FixMessage {
    pub fn new(fields: Vec<(u32, String)>) -> Self {
        Self { fields }
    }

    pub fn get(&self, tag: u32) -> Option<&str> {
        self.fields
            .iter()
            .find(|(t, _)| *t == tag)
            .map(|(_, v)| v.as_str())
    }
    pub fn msg_type(&self) -> Option<&str> {
        self.get(35)
    }

    /// Serialise : enveloppe + corps + checksum, SOH partout.
    pub fn encode(&self) -> Vec<u8> {
        let mut body = Vec::with_capacity(256);
        for (t, v) in &self.fields {
            body.extend_from_slice(t.to_string().as_bytes());
            body.push(b'=');
            body.extend_from_slice(v.as_bytes());
            body.push(SOH);
        }
        let mut out = Vec::with_capacity(body.len() + 32);
        out.extend_from_slice(b"8=FIX.4.4\x01");
        out.extend_from_slice(b"9=");
        out.extend_from_slice(body.len().to_string().as_bytes());
        out.push(SOH);
        let cks = {
            let mut m = out.clone();
            m.extend_from_slice(&body);
            checksum(&m)
        };
        out.extend_from_slice(&body);
        out.extend_from_slice(b"10=");
        out.extend_from_slice(format!("{:03}", cks).as_bytes());
        out.push(SOH);
        out
    }

    /// Parse un message complet. Retourne le message + octets consommes.
    /// Streaming : `Truncated` tant que le buffer est incomplet.
    pub fn decode(buf: &[u8]) -> Result<(Self, usize), CodecError> {
        // --- BeginString
        if buf.len() < 2 {
            return Err(CodecError::Truncated);
        }
        if !buf.starts_with(b"8=") {
            return Err(CodecError::BadBeginString);
        }
        let bs_end = find_soh(buf, 0).ok_or(CodecError::Truncated)?;
        if &buf[2..bs_end] != BEGIN_STRING.as_bytes() {
            return Err(CodecError::BadBeginString);
        }
        // --- BodyLength (9=N<SOH>)
        let bl_tag = bs_end + 1;
        if buf.len() <= bl_tag + 2 {
            return Err(CodecError::Truncated);
        }
        if !buf[bl_tag..].starts_with(b"9=") {
            return Err(CodecError::Malformed);
        }
        let bl_end = find_soh(buf, bl_tag).ok_or(CodecError::Truncated)?;
        let body_len: usize = parse_u32(&buf[bl_tag + 2..bl_end])
            .ok_or(CodecError::BadBodyLength)? as usize;
        let body_start = bl_end + 1;
        let body_end = body_start + body_len;
        // --- trame complete ? (corps + tag 10)
        if buf.len() < body_end + 7 {
            return Err(CodecError::Truncated);
        }
        if !buf[body_end..].starts_with(b"10=") {
            return Err(CodecError::BadBodyLength);
        }
        let cs_end = find_soh(buf, body_end).ok_or(CodecError::Truncated)?;
        let want = parse_u32(&buf[body_end + 3..cs_end]).ok_or(CodecError::Malformed)?;
        let got = checksum(&buf[..body_end]);
        if got != want {
            return Err(CodecError::BadCheckSum { got, want });
        }
        // --- corps -> champs
        let mut fields = Vec::new();
        let mut seen = BTreeMap::new();
        let mut i = body_start;
        while i < body_end {
            let eq = buf[i..body_end]
                .iter()
                .position(|&b| b == b'=')
                .map(|p| i + p)
                .ok_or(CodecError::Malformed)?;
            let tag = parse_u32(&buf[i..eq]).ok_or(CodecError::Malformed)?;
            let end = find_soh(&buf[..body_end], eq + 1)
                .ok_or(CodecError::Malformed)?;
            if end == eq + 1 {
                return Err(CodecError::EmptyValue(tag));
            }
            if seen.insert(tag, ()).is_some() {
                return Err(CodecError::DuplicateTag(tag));
            }
            let val = std::str::from_utf8(&buf[eq + 1..end])
                .map_err(|_| CodecError::Malformed)?
                .to_string();
            fields.push((tag, val));
            i = end + 1;
        }
        Ok((Self { fields }, cs_end + 1))
    }
}

/// Indice du SOH a partir de `from` (relatif a from=0..buf.len()).
fn find_soh(buf: &[u8], from: usize) -> Option<usize> {
    buf[from..].iter().position(|&b| b == SOH).map(|p| from + p)
}
fn parse_u32(b: &[u8]) -> Option<u32> {
    if b.is_empty() || b.len() > 10 || !b.iter().all(|c| c.is_ascii_digit()) {
        return None;
    }
    std::str::from_utf8(b).ok()?.parse().ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn msg(fields: &[(&str, u32)]) -> FixMessage {
        FixMessage::new(fields.iter().map(|(v, t)| (*t, v.to_string())).collect())
    }

    #[test]
    fn roundtrip_encode_decode() {
        let m = msg(&[("A", 35), ("1", 34), ("SENDER", 49), ("TARGET", 56),
                      ("20261102-18:30:00.123", 52), ("EURUSD", 55)]);
        let enc = m.encode();
        // structure : commence par 8=FIX.4.4<SOH>9=, finit par 10=NNN<SOH>
        assert!(enc.starts_with(b"8=FIX.4.4\x019="));
        assert!(enc.ends_with(b"\x01"));
        let (d, used) = FixMessage::decode(&enc).unwrap();
        assert_eq!(used, enc.len());
        assert_eq!(d, m);
        assert_eq!(d.msg_type(), Some("A"));
    }

    #[test]
    fn checksum_is_sum_mod_256() {
        let m = msg(&[("0", 35)]);
        let enc = m.encode();
        // re-calcul manuel : somme octets avant "10=" -> mod 256
        let pos = enc.windows(3).rposition(|w| w == b"10=").unwrap();
        let sum: u32 = enc[..pos].iter().map(|&b| b as u32).sum::<u32>() % 256;
        let want: u32 = std::str::from_utf8(&enc[pos + 3..pos + 6])
            .unwrap().parse().unwrap();
        assert_eq!(sum, want);
    }

    #[test]
    fn decode_rejects_bad_checksum() {
        let mut enc = msg(&[("A", 35), ("X", 55)]).encode();
        // corrompt un octet du corps : le "X" de "55=X" (pas le X de FIX.4.4)
        let pos = enc.windows(2).position(|w| w == b"=X").unwrap();
        enc[pos + 1] = b'Y';
        match FixMessage::decode(&enc) {
            Err(CodecError::BadCheckSum { .. }) => {}
            other => panic!("attendu BadCheckSum, eu {:?}", other),
        }
    }

    #[test]
    fn decode_truncated_is_incomplete_not_error() {
        let enc = msg(&[("A", 35), ("EURUSD", 55)]).encode();
        for n in 1..enc.len() {
            match FixMessage::decode(&enc[..n]) {
                Err(CodecError::Truncated) | Err(CodecError::BadBodyLength)
                | Err(CodecError::Malformed) | Err(CodecError::EmptyValue(_)) => {}
                Err(CodecError::BadCheckSum { .. }) => {
                    panic!("checksum evalue sur trame incomplete a n={}", n)
                }
                Ok(_) => panic!("decode incomplet accepte a n={}", n),
                Err(e) => panic!("erreur inattendue {e:?} a n={n}"),
            }
        }
    }

    #[test]
    fn decode_rejects_duplicate_and_empty() {
        // message brut fabrique a la main (enveloppe + checksum corrects)
        fn raw(body: &str) -> Vec<u8> {
            let mut out = Vec::new();
            out.extend_from_slice(b"8=FIX.4.4\x01");
            out.extend_from_slice(format!("9={}\x01", body.len()).as_bytes());
            out.extend_from_slice(body.as_bytes());
            let cks: u32 = out.iter().map(|&b| b as u32).sum::<u32>() % 256;
            out.extend_from_slice(format!("10={:03}\x01", cks).as_bytes());
            out
        }
        match FixMessage::decode(&raw("35=D\x0135=D\x01")) {
            Err(CodecError::DuplicateTag(35)) => {}
            other => panic!("attendu DuplicateTag, eu {:?}", other),
        }
        match FixMessage::decode(&raw("35=\x01")) {
            Err(CodecError::EmptyValue(35)) => {}
            other => panic!("attendu EmptyValue, eu {:?}", other),
        }
    }

    #[test]
    fn bodylength_mismatch_rejected() {
        // BodyLength ne couvre pas le vrai corps -> tag 10 non trouve
        // a la frontiere annoncee.
        let mut out = Vec::new();
        out.extend_from_slice(b"8=FIX.4.4\x019=2\x0135=D\x01");
        let cks: u32 = out.iter().map(|&b| b as u32).sum::<u32>() % 256;
        out.extend_from_slice(format!("10={:03}\x01", cks).as_bytes());
        match FixMessage::decode(&out) {
            Err(CodecError::BadBodyLength) | Err(CodecError::Truncated) => {}
            other => panic!("attendu rejet bodylength, eu {:?}", other),
        }
    }

    #[test]
    fn streaming_two_messages() {
        let m1 = msg(&[("0", 35), ("1", 34)]).encode();
        let m2 = msg(&[("A", 35), ("2", 34)]).encode();
        let mut stream = m1.clone();
        stream.extend_from_slice(&m2);
        let (d1, n1) = FixMessage::decode(&stream).unwrap();
        let (d2, n2) = FixMessage::decode(&stream[n1..]).unwrap();
        assert_eq!(n1 + n2, stream.len());
        assert_eq!(d1.msg_type(), Some("0"));
        assert_eq!(d2.msg_type(), Some("A"));
    }
}
