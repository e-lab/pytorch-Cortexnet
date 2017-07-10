import torch
from torch import nn
from torch.nn import functional as f
from torch.autograd import Variable as V

from collections import OrderedDict

class CortexNetBase(nn.Module):
    '''
    A slightly modified version of cortexnet model02.
    No classifier attached. 
    '''
    KERNEL_SIZE = 3
    PADDING = KERNEL_SIZE // 2
    KERNEL_STRIDE = 2

    def __init__(self, network_size):
        super().__init__()
        self.nlayers = len(network_size) - 1
        
        for layer in range(self.nlayers):
            # Create D[layer] block
            multiplier = 1 if layer < 1 else 2
            D = nn.Conv2d(in_channels = network_size[layer] * multiplier,
                          out_channels = network_size[layer + 1],
                          kernel_size = CortexNetBase.KERNEL_SIZE,
                          stride = CortexNetBase.KERNEL_STRIDE,
                          padding = CortexNetBase.PADDING)
            D_BN = nn.BatchNorm2d(network_size[layer+1])

            # Create G[layer] block
            G = nn.ConvTranspose2d(in_channels = network_size[layer+1],
                                   out_channels = network_size[layer],
                                   kernel_size = CortexNetBase.KERNEL_SIZE,
                                   stride = CortexNetBase.KERNEL_STRIDE,
                                   padding = CortexNetBase.PADDING,
                                   output_padding=CortexNetBase.PADDING)
            G_BN = nn.BatchNorm2d(network_size[layer])

            setattr(self, 'D_'+str(layer+1), D)
            setattr(self, 'D_'+str(layer+1)+'_BN', D_BN)
            setattr(self, 'G_'+str(layer+1), G)
            setattr(self, 'G_'+str(layer+1)+'_BN', G_BN)

    def forward(self, x, state, all_layers = False):

        residuals = []
        state = state or [None] * (self.nlayers - 1)
        outputs = OrderedDict()

        for layer in range(self.nlayers):
            D = getattr(self, 'D_'+str(layer+1))
            D_BN = getattr(self, 'D_'+str(layer+1)+'_BN')
            if layer > 0:
                if state[layer - 1] is None:
                    s = V(x.data.clone().zero_())
                else:
                    s = state[layer - 1] 
                x = torch.cat((x,s), 1)

            x = D(x)
            residuals.append(x)
            x = f.relu(x)
            x = D_BN(x)
            outputs['D_'+str(layer+1)] = x

        for layer in reversed(range(self.nlayers)):
            G = getattr(self, 'G_'+str(layer+1))
            G_BN = getattr(self, 'G_'+str(layer+1)+'_BN')
            x = G(x)
            if layer > 0:
                state[layer - 1] = x
                x += residuals[layer - 1]
            x = f.relu(x)
            x = G_BN(x)
            outputs['G_'+str(layer+1)] = x

        outputs['residuals'] = residuals
        result = (x, state, outputs) if all_layers else (x, state)
        return result


class CortexNetSeg(CortexNetBase):
    '''
    Base cortex net modified for next frame + segmentation pred
    (assuming atleast two decoder and two generator)
    '''
    def __init__(self, network_size):
        super().__init__(network_size)

        G_2_SEG = nn.ConvTranspose2d(in_channels = self.G_2.in_channels,
                                   out_channels = self.G_2.out_channels,
                                   kernel_size = CortexNetBase.KERNEL_SIZE,
                                   stride = CortexNetBase.KERNEL_STRIDE,
                                   padding = CortexNetBase.PADDING,
                                   output_padding=CortexNetBase.PADDING)
        G_2_SEG_BN = nn.BatchNorm2d(self.G_2.out_channels)

        G_1_SEG = nn.ConvTranspose2d(in_channels = self.G_1.in_channels,
                                     out_channels = 1,
                                     kernel_size = CortexNetBase.KERNEL_SIZE,
                                     stride = CortexNetBase.KERNEL_STRIDE,
                                     padding = CortexNetBase.PADDING,
                                     output_padding=CortexNetBase.PADDING)
        G_1_SEG_BN = nn.BatchNorm2d(1)

        setattr(self, 'G_1_SEG', G_1_SEG)
        setattr(self, 'G_1_SEG_BN', G_1_SEG_BN)
        setattr(self, 'G_2_SEG', G_2_SEG)
        setattr(self, 'G_2_SEG_BN', G_2_SEG_BN)

    def forward(self, x, state, all_layers = False):

        x, state, outputs = super().forward(x, state, True)

        # segmentation g block's input is either the third last G or
        # output of D if only two D,G blocks
        seg_in = outputs['G_3'] if self.nlayers > 2 else outputs['D_2']

        mask = self.G_2_SEG(seg_in)
        mask += outputs['residuals'][0]
        mask = f.relu(mask)
        mask = self.G_2_SEG_BN(mask)
        outputs['G_2_SEG'] = mask
        mask = self.G_1_SEG(mask)
        mask = f.relu(mask)
        mask = self.G_1_SEG_BN(mask)
        outputs['G_1_SEG'] = mask

        result = (x, mask, state, outputs) if all_layers else (x, mask, state)
        return result