"""Library implementing attention modules.

Authors
 * Ju-Chieh Chou 2020
 * Jianyuan Zhong 2020
 * Loren Lugosch 2020
 * Samuele Cornell 2020
"""

import torch
import logging
import torch.nn as nn
import numpy as np
from typing import Optional
from speechbrain.dataio.dataio import length_to_mask
import torch.nn.functional as F

logger = logging.getLogger(__name__)


class ContentBasedAttention(nn.Module):
    """ This class implements content-based attention module for seq2seq
    learning.

    Refereance: NEURAL MACHINE TRANSLATION BY JOINTLY LEARNING TO ALIGN
    AND TRANSLATE, Bahdanau et.al. https://arxiv.org/pdf/1409.0473.pdf

    Arguments
    ---------
    attn_dim : int
        Size of the attention feature.
    output_dim : int
        Size of the output context vector.
    scaling : float
        The factor controls the sharpening degree (default: 1.0).

    Example
    -------
    >>> enc_tensor = torch.rand([4, 10, 20])
    >>> enc_len = torch.ones([4]) * 10
    >>> dec_tensor = torch.rand([4, 25])
    >>> net = ContentBasedAttention(enc_dim=20, dec_dim=25, attn_dim=30, output_dim=5)
    >>> out_tensor, out_weight = net(enc_tensor, enc_len, dec_tensor)
    >>> out_tensor.shape
    torch.Size([4, 5])
    """

    def __init__(self, enc_dim, dec_dim, attn_dim, output_dim, scaling=1.0):
        super(ContentBasedAttention, self).__init__()

        self.mlp_enc = nn.Linear(enc_dim, attn_dim)
        self.mlp_dec = nn.Linear(dec_dim, attn_dim)
        self.mlp_attn = nn.Linear(attn_dim, 1, bias=False)
        self.mlp_out = nn.Linear(enc_dim, output_dim)

        self.scaling = scaling

        self.softmax = nn.Softmax(dim=-1)

        # reset the encoder states, lengths and masks
        self.reset()

    def reset(self):
        """Reset the memory in the attention module.
        """
        self.enc_len = None
        self.precomputed_enc_h = None
        self.mask = None

    def forward(self, enc_states, enc_len, dec_states):
        """Returns the output of the attention module.

        Arguments
        ---------
        enc_states : torch.Tensor
            The tensor to be attended.
        enc_len : torch.Tensor
            The real length (without padding) of enc_states for each sentence.
        dec_states : torch.Tensor
            The query tensor.

        """

        if self.precomputed_enc_h is None:

            self.precomputed_enc_h = self.mlp_enc(enc_states)
            self.mask = length_to_mask(
                enc_len, max_len=enc_states.size(1), device=enc_states.device
            )

        dec_h = self.mlp_dec(dec_states.unsqueeze(1))
        attn = self.mlp_attn(
            torch.tanh(self.precomputed_enc_h + dec_h)
        ).squeeze(-1)

        # mask the padded frames
        attn = attn.masked_fill(self.mask == 0, -np.inf)
        attn = self.softmax(attn * self.scaling)

        # compute context vectors
        # [B, 1, L] X [B, L, F]
        context = torch.bmm(attn.unsqueeze(1), enc_states).squeeze(1)
        context = self.mlp_out(context)

        return context, attn


class LocationAwareAttention(nn.Module):
    """This class implements location-aware attention module for seq2seq learning.

    Reference: Attention-Based Models for Speech Recognition, Chorowski et.al.
    https://arxiv.org/pdf/1506.07503.pdf

    Arguments
    ---------
    attn_dim : int
        Size of the attention feature.
    output_dim : int
        Size of the output context vector.
    conv_channels : int
        Number of channel for location feature.
    kernel_size : int
        Kernel size of convolutional layer for location feature.
    scaling : float
        The factor controls the sharpening degree (default: 1.0).

    Example
    -------
    >>> enc_tensor = torch.rand([4, 10, 20])
    >>> enc_len = torch.ones([4]) * 10
    >>> dec_tensor = torch.rand([4, 25])
    >>> net = LocationAwareAttention(
    ...     enc_dim=20,
    ...     dec_dim=25,
    ...     attn_dim=30,
    ...     output_dim=5,
    ...     conv_channels=10,
    ...     kernel_size=100)
    >>> out_tensor, out_weight = net(enc_tensor, enc_len, dec_tensor)
    >>> out_tensor.shape
    torch.Size([4, 5])
    """

    precomputed_enc_h: Optional[torch.Tensor]

    def __init__(
        self,
        enc_dim,
        dec_dim,
        attn_dim,
        output_dim,
        conv_channels,
        kernel_size,
        scaling=1.0,
    ):
        super(LocationAwareAttention, self).__init__()

        self.mlp_enc = nn.Linear(enc_dim, attn_dim)
        self.mlp_dec = nn.Linear(dec_dim, attn_dim)
        self.mlp_attn = nn.Linear(attn_dim, 1, bias=False)
        self.conv_loc = nn.Conv1d(
            1,
            conv_channels,
            kernel_size=2 * kernel_size + 1,
            padding=kernel_size,
            bias=False,
        )
        self.mlp_loc = nn.Linear(conv_channels, attn_dim)
        self.mlp_attn = nn.Linear(attn_dim, 1, bias=False)
        self.mlp_out = nn.Linear(enc_dim, output_dim)

        self.scaling = scaling

        self.softmax = nn.Softmax(dim=-1)

        # reset the encoder states, lengths and masks
        self.reset()

    def reset(self):
        """Reset the memory in attention module.
        """
        self.enc_len = None
        self.precomputed_enc_h = None
        self.mask = None
        self.prev_attn = None

    def forward(self, enc_states, enc_len, dec_states):
        """Returns the output of the attention module.

        Arguments
        ---------
        enc_states : torch.Tensor
            The tensor to be attended.
        enc_len : torch.Tensor
            The real length (without padding) of enc_states for each sentence.
        dec_states : torch.Tensor
            The query tensor.
        """
        if self.precomputed_enc_h is None:

            self.precomputed_enc_h = self.mlp_enc(enc_states)
            self.mask = length_to_mask(
                enc_len, max_len=enc_states.size(1), device=enc_states.device
            )

            # multiply mask by 1/Ln for each row
            self.prev_attn = self.mask * (1 / enc_len.float()).unsqueeze(1)

        # compute location-aware features
        # [B, 1, L] -> [B, C, L]
        attn_conv = self.conv_loc(self.prev_attn.unsqueeze(1))
        # [B, C, L] -> [B, L, C] -> [B, L, F]
        attn_conv = self.mlp_loc(attn_conv.transpose(1, 2))

        dec_h = self.mlp_dec(dec_states.unsqueeze(1))
        attn = self.mlp_attn(
            torch.tanh(self.precomputed_enc_h + dec_h + attn_conv)
        ).squeeze(-1)

        # mask the padded frames
        attn = attn.masked_fill(self.mask == 0, -np.inf)
        attn = self.softmax(attn * self.scaling)

        # set prev_attn to current attn for the next timestep
        self.prev_attn = attn.detach()

        # compute context vectors
        # [B, 1, L] X [B, L, F]
        context = torch.bmm(attn.unsqueeze(1), enc_states).squeeze(1)
        context = self.mlp_out(context)

        return context, attn


class KeyValueAttention(nn.Module):
    """ This class implements a single-headed key-value attention module for seq2seq
    learning.

    Reference: "Attention Is All You Need" by Vaswani et al., sec. 3.2.1

    Arguments
    ---------
    enc_dim : int
        Size of the encoder feature vectors from which keys and values are computed.
    dec_dim : int
        Size of the decoder feature vectors from which queries are computed.
    attn_dim : int
        Size of the attention feature.
    output_dim : int
        Size of the output context vector.

    Example
    -------
    >>> enc_tensor = torch.rand([4, 10, 20])
    >>> enc_len = torch.ones([4]) * 10
    >>> dec_tensor = torch.rand([4, 25])
    >>> net = KeyValueAttention(enc_dim=20, dec_dim=25, attn_dim=30, output_dim=5)
    >>> out_tensor, out_weight = net(enc_tensor, enc_len, dec_tensor)
    >>> out_tensor.shape
    torch.Size([4, 5])
    """

    def __init__(self, enc_dim, dec_dim, attn_dim, output_dim):
        super(KeyValueAttention, self).__init__()

        self.key_linear = nn.Linear(enc_dim, attn_dim)
        self.query_linear = nn.Linear(dec_dim, attn_dim)
        self.value_linear = nn.Linear(enc_dim, output_dim)
        self.scaling = torch.sqrt(torch.tensor(attn_dim).float())

        # reset the encoder states, lengths and masks
        self.reset()

    def reset(self):
        """Reset the memory in the attention module.
        """
        self.values = None
        self.keys = None
        self.mask = None

    def forward(self, enc_states, enc_len, dec_states):
        """Returns the output of the attention module.

        Arguments
        ---------
        enc_states : torch.Tensor
            The tensor to be attended.
        enc_len : torch.Tensor
            The real length (without padding) of enc_states for each sentence.
        dec_states : torch.Tensor
            The query tensor.
        """

        if self.keys is None:

            self.keys = self.key_linear(enc_states)
            self.values = self.value_linear(enc_states)
            self.mask = length_to_mask(
                enc_len, max_len=enc_states.size(1), device=enc_states.device
            ).unsqueeze(2)

        query = self.query_linear(dec_states).unsqueeze(2)
        scores = torch.matmul(self.keys, query) / self.scaling
        scores = scores.masked_fill(self.mask == 0, -np.inf)
        normalized_scores = scores.softmax(1).transpose(1, 2)
        out = torch.matmul(normalized_scores, self.values).squeeze(1)
        return out, normalized_scores


class RelPosMultiHeadAttention(nn.Module):
    def __init__(
        self,
        embed_dim,
        num_heads,
        head_dim=None,
        dropout=0.0,
        bias=False,
        rr_bias=None,
        rw_bias=None,
    ):
        super().__init__()
        self.embed_dim = embed_dim
        self.num_heads = num_heads
        self.dropout = nn.Dropout(dropout)

        if head_dim is None:
            self.head_dim = embed_dim // num_heads
            assert (
                self.head_dim * num_heads == self.embed_dim
            ), "embed_dim must be divisible by num_heads"
        else:
            self.head_dim = head_dim

        self.q_proj = nn.Linear(
            embed_dim, self.head_dim * self.num_heads, bias=False
        )
        self.k_proj = nn.Linear(
            embed_dim, self.head_dim * self.num_heads, bias=False
        )
        self.v_proj = nn.Linear(
            embed_dim, self.head_dim * self.num_heads, bias=bias
        )
        self.r_proj = nn.Linear(
            embed_dim, self.head_dim * self.num_heads, bias=False
        )
        self.out_proj = nn.Linear(
            self.head_dim * self.num_heads, embed_dim, bias=bias
        )

        self.scale = 1 / (self.head_dim ** 0.5)

        if (
            rr_bias is None or rw_bias is None
        ):  # Learned position biases are not shared
            self.r_r_bias = nn.Parameter(
                torch.FloatTensor(self.num_heads, self.head_dim)
            )
            self.r_w_bias = nn.Parameter(
                torch.FloatTensor(self.num_heads, self.head_dim)
            )
            self.rel_shared = False
        else:
            self.r_r_bias = rr_bias
            self.r_w_bias = rw_bias
            self.rel_shared = True

        self._init_params()

        if next(self.parameters()).dtype == torch.float16:
            self.attn_fill_value = -65000
        else:
            self.attn_fill_value = -1e30

    def _init_params(self):

        torch.nn.init.xavier_uniform_(self.q_proj.weight)
        torch.nn.init.xavier_uniform_(self.k_proj.weight)
        torch.nn.init.xavier_uniform_(self.v_proj.weight)
        torch.nn.init.xavier_uniform_(self.r_proj.weight)
        if not self.rel_shared:
            torch.nn.init.xavier_uniform_(self.r_r_bias)
            torch.nn.init.xavier_uniform_(self.r_w_bias)

    def _rel_shift(self, x):

        zero_pad_shape = (x.size(0), 1) + x.size()[2:]
        zero_pad = torch.zeros(zero_pad_shape, device=x.device, dtype=x.dtype)
        x_padded = torch.cat([zero_pad, x], dim=1)
        x_padded_shape = (x.size(1) + 1, x.size(0)) + x.size()[2:]
        x_padded = x_padded.view(*x_padded_shape)
        x = x_padded[1:].view_as(x)

        return x

    def forward(
        self, query, key, value, r, key_padding_mask=None, attn_mask=None
    ):

        qlen, bsz, embed_dim = query.size()
        klen = key.size(0)
        assert bsz == value.size(1) == query.size(1)
        assert klen == value.size(0)
        assert r.size(0) == klen

        if key_padding_mask is not None:
            assert key_padding_mask.size(0) == bsz
            assert key_padding_mask.size(1) == klen

        k = self.k_proj(key).view(klen, bsz, self.num_heads, self.head_dim)
        v = self.v_proj(value).view(klen, bsz, self.num_heads, self.head_dim)
        q = self.q_proj(query).view(qlen, bsz, self.num_heads, self.head_dim)

        r = self.r_proj(r).view(
            klen, self.num_heads, self.head_dim
        )  # for rel_pos
        # qlen x num_heads x d_head

        # compute attention score
        r_w_q = q + self.r_w_bias  # qlen x bsz x num_heads x d_head
        AC = torch.einsum(
            "ibnd,jbnd->ijbn", (r_w_q, k)
        )  # qlen x klen x bsz x num_heads

        r_r_q = q + self.r_r_bias
        BD = torch.einsum(
            "ibnd,jnd->ijbn", (r_r_q, r)
        )  # qlen x klen x bsz x num_heads
        BD = self._rel_shift(BD)

        # [qlen x klen x bsz x num_heads]
        attn_score = AC + BD
        attn_score.mul_(self.scale)

        # compute attention probability
        if attn_mask is not None and torch.sum(attn_mask).item():
            if attn_mask.dim() == 2:
                attn_score = attn_score.masked_fill(
                    attn_mask[None, :, :, None], self.attn_fill_value
                )
            elif attn_mask.dim() == 3:
                attn_score = attn_score.masked_fill(
                    attn_mask[:, :, :, None], self.attn_fill_value
                )

        if key_padding_mask is not None:
            attn_score = attn_score.masked_fill(
                key_padding_mask.transpose(0, 1)
                .unsqueeze(0)
                .reshape(1, klen, bsz, 1),
                self.attn_fill_value,
            )

        # [qlen x klen x bsz x num_heads]
        attn_prob = F.softmax(attn_score, dim=1)
        attn_prob = self.dropout(attn_prob)

        # Mask attention
        if attn_mask is not None:
            attn_prob = attn_prob * attn_mask

        # compute attended vector
        attn_vec = torch.einsum("ijbn,jbnd->ibnd", (attn_prob, v))

        # [qlen x bsz x num_heads x d_head]
        attn_vec = attn_vec.contiguous().view(
            attn_vec.size(0), attn_vec.size(1), self.num_heads * self.head_dim
        )

        # linear projection
        attn_out = self.out_proj(attn_vec)

        return attn_out, attn_prob


class MultiheadAttention(nn.Module):
    """ The class is a wrapper of MultiHead Attention for torch.nn.MultiHeadAttention.

    Reference: https://pytorch.org/docs/stable/nn.html

    Arguments
    ----------
    num_heads : int
        parallel attention heads.
    dropout : float
        a Dropout layer on attn_output_weights (default: 0.0).
    bias : bool
        add bias as module parameter (default: True).
    add_bias_kv : bool
        add bias to the key and value sequences at dim=0.
    add_zero_attn : bool
        add a new batch of zeros to the key and value sequences at dim=1.
    kdim : int
        total number of features in key (default: None).
    vdim : int
        total number of features in value (default: None).

    Example
    -------
    >>> inputs = torch.rand([8, 60, 512])
    >>> net = MultiheadAttention(nhead=8, d_model=inputs.shape[-1])
    >>> outputs, attn = net(inputs, inputs, inputs)
    >>> outputs.shape
    torch.Size([8, 60, 512])
    """

    def __init__(
        self,
        nhead,
        d_model,
        dropout=0.0,
        bias=True,
        add_bias_kv=False,
        add_zero_attn=False,
        kdim=None,
        vdim=None,
    ):
        super().__init__()

        self.att = nn.MultiheadAttention(
            embed_dim=d_model,
            num_heads=nhead,
            dropout=dropout,
            bias=bias,
            add_bias_kv=add_bias_kv,
            add_zero_attn=add_zero_attn,
            kdim=kdim,
            vdim=vdim,
        )

    def forward(
        self,
        query,
        key,
        value,
        attn_mask: Optional[torch.Tensor] = None,
        key_padding_mask: Optional[torch.Tensor] = None,
    ):
        """
        Arguments
        ----------
        query : tensor
            (L, N, E) where L is the target sequence length,
            N is the batch size, E is the embedding dimension.
        key : tensor
            (S, N, E) where S is the source sequence length,
            N is the batch size, E is the embedding dimension.
        value : tensor
            (S, N, E) where S is the source sequence length,
            N is the batch size, E is the embedding dimension.
        key_padding_mask : tensor
            (N, S) where N is the batch size, S is the source sequence
            length. If a ByteTensor is provided, the non-zero positions will
            be ignored while the position with the zero positions will be
            unchanged. If a BoolTensor is provided, the positions with the
            value of True will be ignored while the position with the value
            of False will be unchanged.
        attn_mask : tensor
            2D mask (L, S) where L is the target sequence length, S is
            the source sequence length.
            3D mask (N*num_heads, L, S) where N is the batch
            size, L is the target sequence length, S is the source sequence
            length. attn_mask ensure that position i is allowed to attend the
            unmasked positions. If a ByteTensor is provided, the non-zero
            positions are not allowed to attend while the zero positions will
            be unchanged. If a BoolTensor is provided, positions with True is
            not allowed to attend while False values will be unchanged. If a
            FloatTensor is provided, it will be added to the attention weight.

        Outputs
        -------
        attn_output : tensor
            (L, N, E) where L is the target sequence length, N is the
            batch size, E is the embedding dimension.
        attn_output_weights : tensor
            (N, L, S) where N is the batch size, L is the target
            sequence length, S is the source sequence length.
        """
        # give tensors of shape (time, batch, fea)
        query = query.permute(1, 0, 2)
        key = key.permute(1, 0, 2)
        value = value.permute(1, 0, 2)

        output, attention = self.att(
            query,
            key,
            value,
            attn_mask=attn_mask,
            key_padding_mask=key_padding_mask,
        )

        # reshape the output back to (batch, time, fea)
        output = output.permute(1, 0, 2)

        return output, attention


class PositionalwiseFeedForward(nn.Module):
    """The class implements the positional-wise feed forward module in
    “Attention Is All You Need”.

    Arguments
    ----------
    d_ffn: int
        Dimension of representation space of this positional-wise feed
        forward module.
    input_shape : tuple
        Expected shape of the input. Alternatively use ``input_size``.
    input_size : int
        Expected size of the input. Alternatively use ``input_shape``.
    dropout: float
        Fraction of outputs to drop.
    activation: torch class
        activation functions to be applied (Recommendation: ReLU, GELU).

    Example
    -------
    >>> inputs = torch.rand([8, 60, 512])
    >>> net = PositionalwiseFeedForward(256, input_size=inputs.shape[-1])
    >>> outputs = net(inputs)
    >>> outputs.shape
    torch.Size([8, 60, 512])
    """

    def __init__(
        self,
        d_ffn,
        input_shape=None,
        input_size=None,
        dropout=0.1,
        activation=nn.ReLU,
    ):
        super().__init__()

        if input_shape is None and input_size is None:
            raise ValueError("Expected one of input_shape or input_size")

        if input_size is None:
            input_size = input_shape[-1]

        self.ffn = nn.Sequential(
            nn.Linear(input_size, d_ffn),
            activation(),
            nn.Dropout(dropout),
            nn.Linear(d_ffn, input_size),
        )

    def forward(self, x):
        # give a tensor of shap (time, batch, fea)
        x = x.permute(1, 0, 2)
        x = self.ffn(x)

        # reshape the output back to (batch, time, fea)
        x = x.permute(1, 0, 2)

        return x
