# flake8: noqa

from .variable import (
    VariableBehavior,
    RadiusVariable,
    ConicVariable,
    ThicknessVariable,
    IndexVariable,
    AsphereCoeffVariable,
    PolynomialCoeffVariable,
    ChebyshevCoeffVariable,
    Variable,
)
from .operand import ParaxialOperand, AberrationOperand, RayOperand, Operand
from .problem import OptimizationProblem
from .material_candidates import (
    MaterialCandidate,
    MaterialPool,
    MaterialReference,
    prepare_material_candidate,
    validate_material_spectrum,
)
from .glass_expert_records import (
    ContinuousValue,
    MaterialChoice,
    GlassExpertState,
    GlassExpertTrial,
    GlassExpertProgress,
    GlassExpertReport,
    GlassExpertCancelled,
)
from .optimizer.scipy import (
    OptimizerGeneric,
    LeastSquares,
    DualAnnealing,
    DifferentialEvolution,
    SHGO,
    BasinHopping,
    GlassExpert,
    OrthogonalDescent,
)

try:
    from .optimizer.torch.adam import TorchAdamOptimizer
    from .optimizer.torch.sgd import TorchSGDOptimizer
except (ImportError, ModuleNotFoundError, OSError):
    pass

from .optimizer.scipy import glass_expert

from .optimizer.custom import (
    CMAES,
    ParticleSwarm,
)

import sys

optimization = sys.modules[__name__]
