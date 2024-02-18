from typing import Callable, Optional

import torch
import torch.nn as nn
import torch.nn.functional as F

from aqlm.utils import _dequantize_weight, unpack_int_data


def get_forward_pass_kernel(
    codebooks: torch.Tensor,
) -> Callable[[torch.Tensor, torch.IntTensor, torch.Tensor, torch.Tensor, Optional[torch.Tensor]], torch.Tensor]:
    num_codebooks, codebook_size, out_group_size, in_group_size = codebooks.shape
    match (codebooks.device.type, num_codebooks, codebook_size, out_group_size, in_group_size):
        case ("cuda", 1, 65536, 1, 8):
            from .cuda_kernel import CUDA_FOLDER

            assert (
                codebooks.dtype == torch.float16
            ), f"please load the model with `torch_dtype=torch.float16`, as {codebooks.dtype} is not supported on GPU yet"
            return torch.ops.aqlm_cuda_kernel.code1x16_matmat
        case ("cuda", 2, 256, 1, 8):
            from .cuda_kernel import CUDA_FOLDER

            assert (
                codebooks.dtype == torch.float16
            ), f"please load the model with `torch_dtype=torch.float16`, as {codebooks.dtype} is not supported on GPU yet"
            return torch.ops.aqlm_cuda_kernel.code2x8_matmat
        case ("cuda", _, _, 1, _):
            from .triton_kernel import triton_matmul

            return triton_matmul
        case ("cpu", _, 256, 1, _):
            from .numba_kernel import numba_gemm_lut

            return numba_gemm_lut
        case _:
            from .dequantization import dequantize_gemm

            return dequantize_gemm


def get_backward_pass_kernel(
    codebooks: torch.Tensor,
) -> torch.Tensor:
    forward_pass_kernel = get_forward_pass_kernel(codebooks=codebooks)

    def _backward_pass_kernel(
        grad_output: torch.Tensor,  #  [..., in_features]
        codes: torch.IntTensor,  #  [num_out_groups, num_in_groups, num_codebooks]
        codebooks: torch.Tensor,  #  [num_codebooks, codebook_size, out_group_size, in_group_size]
        scales: torch.Tensor,  #  [num_out_groups, 1, 1, 1]
        bias: Optional[torch.Tensor],
    ) -> torch.Tensor:
        return forward_pass_kernel(
            grad_output.contiguous(),
            codes.transpose(0, 1).contiguous(),
            codebooks.transpose(2, 3).contiguous(),
            scales.transpose(0, 1).transpose(2, 3).contiguous(),
            None,
        )

    return _backward_pass_kernel
