//! Enveloppe `ProtoMessage` + trame 4 octets Big-Endian.
//!
//! La trame cTrader = `u32 BE` (taille du protobuf) + protobuf. Pas de
//! marqueur de début/fin : tout repose sur la taille exacte.

use std::borrow::Cow;

use quacky::{BytesReader, BytesWriter, MessageRead, MessageWrite, Writer};

use crate::err::ClientError;
use crate::proto::spotware::ProtoMessage;
use crate::MAX_FRAME_SIZE;

/// Encode une `ProtoMessage` en protobuf (sans l'en-tête de trame).
pub fn encode_msg(msg: &ProtoMessage<'_>) -> Result<Vec<u8>, ClientError> {
    let mut buf = vec![0u8; msg.get_size()];
    let mut w = Writer::new(BytesWriter::new(&mut buf));
    msg.write_message(&mut w)
        .map_err(|e| ClientError::Proto(e.to_string()))?;
    Ok(buf)
}

/// Décode une `ProtoMessage` depuis un payload protobuf.
pub fn decode_msg(buf: &[u8]) -> Result<ProtoMessage<'static>, ClientError> {
    let owned: Vec<u8> = buf.to_vec();
    let mut r = BytesReader::from_bytes(&owned);
    let m = ProtoMessage::from_reader(&mut r, &owned)
        .map_err(|e| ClientError::Proto(e.to_string()))?;
    // re-owning : les Cow empruntent `owned` -> convertir en Owned
    Ok(ProtoMessage {
        payloadType: m.payloadType,
        payload: m.payload.map(|c| Cow::Owned(c.into_owned())),
        clientMsgId: m.clientMsgId.map(|c| Cow::Owned(c.into_owned())),
    })
}

/// Construit l'enveloppe autour d'un payload métier déjà encodé.
pub fn envelope<'a>(
    payload_type: crate::proto::spotware::ProtoOAPayloadType,
    payload: Vec<u8>,
    client_msg_id: Option<&'a str>,
) -> ProtoMessage<'a> {
    ProtoMessage {
        payloadType: payload_type as u32,
        payload: Some(Cow::Owned(payload)),
        clientMsgId: client_msg_id.map(Cow::Borrowed),
    }
}

/// Trame complète prête pour la socket : `u32 BE len` + protobuf.
pub fn frame(msg: &ProtoMessage<'_>) -> Result<Vec<u8>, ClientError> {
    let body = encode_msg(msg)?;
    if body.len() > MAX_FRAME_SIZE {
        return Err(ClientError::FrameTooLong(body.len()));
    }
    let mut out = Vec::with_capacity(4 + body.len());
    out.extend_from_slice(&(body.len() as u32).to_be_bytes());
    out.extend_from_slice(&body);
    Ok(out)
}

/// Encode un message métier `MessageWrite` puis enveloppe + trame complète.
pub fn frame_payload<M: MessageWrite>(
    payload_type: crate::proto::spotware::ProtoOAPayloadType,
    msg: &M,
    client_msg_id: Option<&str>,
) -> Result<Vec<u8>, ClientError> {
    let mut buf = vec![0u8; msg.get_size()];
    let mut w = Writer::new(BytesWriter::new(&mut buf));
    msg.write_message(&mut w)
        .map_err(|e| ClientError::Proto(e.to_string()))?;
    frame(&envelope(payload_type, buf, client_msg_id))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::proto::spotware::{
        ProtoOAPayloadType, ProtoOAApplicationAuthReq, ProtoOASpotEvent, ProtoPayloadType,
    };

    #[test]
    fn roundtrip_heartbeat() {
        // heartbeat = payloadType 51, payload vide, pas de clientMsgId
        let m = ProtoMessage {
            payloadType: ProtoPayloadType::HEARTBEAT_EVENT as u32,
            payload: Some(Cow::Owned(vec![])),
            clientMsgId: None,
        };
        let f = frame(&m).unwrap();
        let len = u32::from_be_bytes(f[..4].try_into().unwrap()) as usize;
        assert_eq!(len, f.len() - 4);
        let d = decode_msg(&f[4..]).unwrap();
        assert_eq!(d.payloadType, 51);
    }

    #[test]
    fn roundtrip_business_msg() {
        let req = ProtoOAApplicationAuthReq {
            payloadType: Some(ProtoOAPayloadType::PROTO_OA_APPLICATION_AUTH_REQ),
            clientId: "id-x".into(),
            clientSecret: "secret-y".into(),
        };
        let f = frame_payload(
            ProtoOAPayloadType::PROTO_OA_APPLICATION_AUTH_REQ,
            &req,
            Some("cmid-1"),
        )
        .unwrap();
        let len = u32::from_be_bytes(f[..4].try_into().unwrap()) as usize;
        let env = decode_msg(&f[4..4 + len]).unwrap();
        assert_eq!(
            env.payloadType,
            ProtoOAPayloadType::PROTO_OA_APPLICATION_AUTH_REQ as u32
        );
        assert_eq!(env.clientMsgId.as_deref(), Some("cmid-1"));
        // ré-decode le payload métier
        let payload = env.payload.as_ref().unwrap();
        let mut r = BytesReader::from_bytes(payload);
        let back = ProtoOAApplicationAuthReq::from_reader(&mut r, payload).unwrap();
        assert_eq!(back.clientId, "id-x");
        assert_eq!(back.clientSecret, "secret-y");
    }

    #[test]
    fn frame_length_is_big_endian() {
        let m = ProtoMessage {
            payloadType: 51,
            payload: None,
            clientMsgId: Some("abcdefghij".into()), // 10 octets -> taille ~16
        };
        let f = frame(&m).unwrap();
        // le 4e octet doit être la partie basse de la longueur (BE)
        let n = u32::from_be_bytes(f[..4].try_into().unwrap());
        assert_eq!(n as usize, f.len() - 4);
        assert!(f.len() > 10);
    }

    #[test]
    fn decode_spot_event_fields() {
        let ev = ProtoOASpotEvent {
            payloadType: Some(ProtoOAPayloadType::PROTO_OA_SPOT_EVENT),
            ctidTraderAccountId: 42,
            symbolId: 7,
            bid: Some(118347),
            ask: Some(118350),
            timestamp: Some(1_700_000_000_000),
            ..Default::default()
        };
        let f = frame_payload(ProtoOAPayloadType::PROTO_OA_SPOT_EVENT, &ev, None).unwrap();
        let env = decode_msg(&f[4..]).unwrap();
        assert_eq!(env.payloadType, 2131);
        let p = env.payload.as_ref().unwrap();
        let mut r = BytesReader::from_bytes(p);
        let back = ProtoOASpotEvent::from_reader(&mut r, p).unwrap();
        assert_eq!(back.symbolId, 7);
        assert_eq!(back.bid, Some(118347));
        assert_eq!(back.timestamp, Some(1_700_000_000_000));
    }
}
