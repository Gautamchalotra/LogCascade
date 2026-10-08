"""Sequence autoencoders over Event-ID windows.

Both models map (B,T) int ids -> (logits (B,T,V), latent (B,L)).
Reconstruction error of a window = mean token cross-entropy over non-PAD positions.
"""
from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.nn.utils.rnn import pack_padded_sequence, pad_packed_sequence

PAD_ID = 0


class LSTMAutoencoder(nn.Module):
    def __init__(self, vocab_size, embed_dim=32, hidden_dim=64, latent_dim=16,
                 num_layers=1, dropout=0.1, pad_id=PAD_ID):
        super().__init__()
        self.pad_id = pad_id
        d = dropout if num_layers > 1 else 0.0
        self.embed = nn.Embedding(vocab_size, embed_dim, padding_idx=pad_id)
        self.encoder = nn.LSTM(embed_dim, hidden_dim, num_layers, batch_first=True, dropout=d)
        self.to_latent = nn.Linear(hidden_dim, latent_dim)
        self.from_latent = nn.Linear(latent_dim, hidden_dim)
        self.decoder = nn.LSTM(hidden_dim, hidden_dim, num_layers, batch_first=True, dropout=d)
        self.out = nn.Linear(hidden_dim, vocab_size)

    def forward(self, x):
        T = x.size(1)
        lengths = (x != self.pad_id).sum(1).clamp(min=1).cpu()
        packed = pack_padded_sequence(self.embed(x), lengths, batch_first=True, enforce_sorted=False)
        _, (h, _) = self.encoder(packed)
        z = self.to_latent(h[-1])                                   # bottleneck
        dec_in = self.from_latent(z).unsqueeze(1).expand(-1, T, -1)  # latent repeated per step
        out, _ = self.decoder(dec_in.contiguous())
        return self.out(out), z


class TransformerAutoencoder(nn.Module):
    def __init__(self, vocab_size, window_size, d_model=64, nhead=4, num_layers=2,
                 ff_dim=128, latent_dim=16, dropout=0.1, pad_id=PAD_ID):
        super().__init__()
        self.pad_id = pad_id
        self.embed = nn.Embedding(vocab_size, d_model, padding_idx=pad_id)
        self.pos = nn.Parameter(torch.randn(1, window_size, d_model) * 0.02)

        def stack():
            layer = nn.TransformerEncoderLayer(d_model, nhead, ff_dim, dropout,
                                               batch_first=True, norm_first=True)
            return nn.TransformerEncoder(layer, num_layers, enable_nested_tensor=False)

        self.encoder, self.decoder = stack(), stack()
        self.window_size = window_size
        self.to_latent = nn.Linear(window_size * d_model, latent_dim)
        self.from_latent = nn.Linear(latent_dim, d_model)
        self.out = nn.Linear(d_model, vocab_size)

    def forward(self, x):
        T = x.size(1)
        pad = x == self.pad_id
        h = self.encoder(self.embed(x) + self.pos[:, :T], src_key_padding_mask=pad)
        h = h * (~pad).unsqueeze(-1).float()                         # zero PAD positions
        z = self.to_latent(h.flatten(1))                             # bottleneck (keeps positional info)
        d = self.from_latent(z).unsqueeze(1) + self.pos[:, :T]       # decode from latent + positions only
        return self.out(self.decoder(d, src_key_padding_mask=pad)), z


def build_model(kind: str, vocab_size: int, window_size: int, params: dict) -> nn.Module:
    if kind == "lstm":
        return LSTMAutoencoder(vocab_size, **params)
    if kind == "transformer":
        return TransformerAutoencoder(vocab_size, window_size, **params)
    raise ValueError(f"unknown model kind {kind!r}")


def per_token_ce(logits, x, pad_id=PAD_ID):
    return F.cross_entropy(logits.transpose(1, 2), x, reduction="none", ignore_index=pad_id)


def window_error(ce, x, pad_id=PAD_ID):
    mask = x != pad_id
    return ce.sum(1) / mask.sum(1).clamp(min=1)
