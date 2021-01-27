"""Vanilla Neural Network for simple tests

Authors
* Elena Rastorgueva 2020
"""
import torch
import speechbrain as sb


class VanillaNN(sb.nnet.containers.Sequential):
    """A simple vanilla Deep Neural Network.

    Arguments
    ---------
    activation : torch class
        A class used for constructing the activation layers.
    dnn_blocks : int
        The number of linear neural blocks to include.
    dnn_neurons : int
        The number of neurons in the linear layers.

    Example
    -------
    >>> inputs = torch.rand([10, 120, 60])
    >>> model = VanillaNN(input_shape=inputs.shape)
    >>> outputs = model(inputs)
    >>> outputs.shape
    torch.Size([10, 120, 512])
    """

    def __init__(
        self,
        input_shape,
        activation=torch.nn.LeakyReLU,
        dnn_blocks=2,
        dnn_neurons=512,
    ):
        super().__init__(input_shape=input_shape)

        for block_index in range(dnn_blocks):
            self.append(
                sb.nnet.linear.Linear,
                n_neurons=dnn_neurons,
                bias=True,
                layer_name="linear",
            )
            self.append(activation(), layer_name="act")
