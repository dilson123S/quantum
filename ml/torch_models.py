"""PyTorch Deep Learning Neural Architectures for Trading Signal Prediction and System-1 Reasoning.

Includes:
1. TemporalGRUPredictor: Sequence-aware GRU + Multi-Head Attention for market direction and success probability.
2. NanoJevPyTorchNet: Deep Residual MLP for System-1 state evaluation (thesis alignment, evidence score, trap risk).
"""
from __future__ import annotations

import math
from typing import Dict, Optional, Tuple, Union
import torch
import torch.nn as nn
import torch.nn.functional as F


class SelfAttentionPooling(nn.Module):
    """Self-attention pooling layer to aggregate sequential hidden states."""

    def __init__(self, hidden_dim: int):
        super().__init__()
        self.query = nn.Linear(hidden_dim, 1, bias=False)

    def forward(self, x: torch.Tensor, mask: Optional[torch.Tensor] = None) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Args:
            x: Tensor of shape (batch_size, seq_len, hidden_dim)
            mask: Optional mask of shape (batch_size, seq_len)
        Returns:
            pooled: (batch_size, hidden_dim)
            weights: (batch_size, seq_len, 1)
        """
        scores = self.query(x)  # (batch_size, seq_len, 1)
        if mask is not None:
            scores = scores.masked_fill(mask.unsqueeze(-1) == 0, -1e9)
        weights = F.softmax(scores, dim=1)
        pooled = torch.sum(x * weights, dim=1)  # (batch_size, hidden_dim)
        return pooled, weights


class ResidualDenseBlock(nn.Module):
    """Residual dense block with LayerNorm, Mish/GELU activation and Dropout."""

    def __init__(self, dim: int, dropout: float = 0.2):
        super().__init__()
        self.net = nn.Sequential(
            nn.LayerNorm(dim),
            nn.Linear(dim, dim * 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(dim * 2, dim),
            nn.Dropout(dropout),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x + self.net(x)


class TemporalGRUPredictor(nn.Module):
    """Temporal GRU Neural Network with Attention Pooling for Financial Signal Prediction.
    
    Can operate on:
    - 2D Input: (batch_size, num_features) for tabular bar-by-bar features.
    - 3D Input: (batch_size, seq_len, num_features) for multi-bar temporal sequences.
    
    Outputs:
    - direction_logits: (batch_size, 3) -> [SELL (-1), HOLD (0), BUY (1)]
    - success_prob: (batch_size, 1) -> Sigmoid P(Trade Success | Entry)
    - volatility_pred: (batch_size, 1) -> Softplus expected forward volatility
    """

    def __init__(
        self,
        num_features: int = 15,
        hidden_dim: int = 64,
        num_layers: int = 2,
        dropout: float = 0.2,
        bidirectional: bool = True,
    ):
        super().__init__()
        self.num_features = num_features
        self.hidden_dim = hidden_dim
        self.bidirectional = bidirectional
        self.num_directions = 2 if bidirectional else 1

        # Input Normalization
        self.input_norm = nn.LayerNorm(num_features)
        self.input_proj = nn.Linear(num_features, hidden_dim)

        # Recurrent Temporal Backbone
        self.gru = nn.GRU(
            input_size=hidden_dim,
            hidden_size=hidden_dim,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
            bidirectional=bidirectional,
        )

        gru_out_dim = hidden_dim * self.num_directions
        self.attention = SelfAttentionPooling(gru_out_dim)

        # Representation Trunk
        self.trunk = nn.Sequential(
            nn.Linear(gru_out_dim, hidden_dim),
            ResidualDenseBlock(hidden_dim, dropout=dropout),
            ResidualDenseBlock(hidden_dim, dropout=dropout),
            nn.LayerNorm(hidden_dim),
        )

        # Multi-Task Prediction Heads
        self.direction_head = nn.Linear(hidden_dim, 3)     # 3 classes: [SELL, HOLD, BUY]
        self.success_head = nn.Linear(hidden_dim, 1)       # P(success) logits
        self.volatility_head = nn.Linear(hidden_dim, 1)    # Predicted forward volatility

    def forward(
        self,
        x: torch.Tensor,
        return_dict: bool = True,
    ) -> Union[Tuple[torch.Tensor, torch.Tensor, torch.Tensor], Dict[str, torch.Tensor]]:
        # Handle 2D tabular input (batch_size, features) by expanding to (batch_size, 1, features)
        if x.dim() == 2:
            x = x.unsqueeze(1)

        batch_size, seq_len, _ = x.shape
        x_norm = self.input_norm(x)
        x_proj = F.gelu(self.input_proj(x_norm)) if hasattr(F, "gelu") else F.relu(self.input_proj(x_norm))

        gru_out, _ = self.gru(x_proj)
        pooled, attn_weights = self.attention(gru_out)
        latent = self.trunk(pooled)

        direction_logits = self.direction_head(latent)
        success_logits = self.success_head(latent)
        success_prob = torch.sigmoid(success_logits)
        volatility_pred = F.softplus(self.volatility_head(latent)) + 1e-4

        if return_dict:
            return {
                "direction_logits": direction_logits,
                "direction_probs": F.softmax(direction_logits, dim=-1),
                "success_prob": success_prob,
                "volatility": volatility_pred,
                "attention_weights": attn_weights,
            }
        return direction_logits, success_prob, volatility_pred


class NanoJevPyTorchNet(nn.Module):
    """Deep Residual Neural Network for System-1 Decision Validation (NanoJev 0.6B Engine).
    
    Evaluates market states across Deriv Synthetics, Gold, and Forex:
    - Inputs: 16-dimensional normalized market evidence vector.
    - Outputs:
      * thesis_logits: 3 classes -> [ALIGNED, MIXED, CONTRADICTORY]
      * evidence_score: 5 classes -> [1, 2, 3, 4, 5]
      * trap_logits: 3 classes -> [GENUINE_EXPANSION, POTENTIAL_TRAP, OVEREXTENDED_EXHAUSTION]
      * confidence: calibrated probability in [0, 1]
    """

    def __init__(
        self,
        input_dim: int = 16,
        hidden_dim: int = 64,
        dropout: float = 0.15,
    ):
        super().__init__()
        self.input_dim = input_dim

        self.input_layer = nn.Sequential(
            nn.LayerNorm(input_dim),
            nn.Linear(input_dim, hidden_dim),
            nn.GELU(),
        )

        self.res1 = ResidualDenseBlock(hidden_dim, dropout=dropout)
        self.res2 = ResidualDenseBlock(hidden_dim, dropout=dropout)
        self.norm_final = nn.LayerNorm(hidden_dim)

        # Output Heads
        self.thesis_head = nn.Linear(hidden_dim, 3)     # ALIGNED (0), MIXED (1), CONTRADICTORY (2)
        self.evidence_head = nn.Linear(hidden_dim, 5)   # Evidence scores 1 to 5
        self.trap_head = nn.Linear(hidden_dim, 3)       # GENUINE_EXPANSION (0), POTENTIAL_TRAP (1), OVEREXTENDED (2)
        self.conf_head = nn.Linear(hidden_dim, 1)       # Continuous confidence score

    def forward(self, x: torch.Tensor) -> Dict[str, torch.Tensor]:
        if x.dim() == 1:
            x = x.unsqueeze(0)

        h = self.input_layer(x)
        h = self.res1(h)
        h = self.res2(h)
        h = self.norm_final(h)

        thesis_logits = self.thesis_head(h)
        evidence_logits = self.evidence_head(h)
        trap_logits = self.trap_head(h)
        confidence = torch.sigmoid(self.conf_head(h))

        return {
            "thesis_logits": thesis_logits,
            "thesis_probs": F.softmax(thesis_logits, dim=-1),
            "evidence_logits": evidence_logits,
            "evidence_score": torch.argmax(evidence_logits, dim=-1) + 1,  # 1 to 5
            "trap_logits": trap_logits,
            "trap_probs": F.softmax(trap_logits, dim=-1),
            "confidence": confidence,
        }
