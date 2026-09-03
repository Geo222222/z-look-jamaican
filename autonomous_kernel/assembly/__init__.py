"""Evidence-weighted multi-model intelligence assembly.

Z8 combines eligible Z3 component predictions using Z5 lifecycle state and Z7
competence. Assembly can publish another Z3 prediction, but it has no authority
to allocate capital, authorize risk, or execute through The Hand.
"""

from .adaptive import AdaptiveAssemblyError, assemble_prediction
from .contracts import (
    ASSEMBLY_SCHEMA_VERSION,
    INTERVAL_POLICY_ID,
    WEIGHT_POLICY_ID,
    AssemblyContractError,
    AssemblyReceipt,
)
from .journal import AssemblyJournal, AssemblyJournalError, validate_assembly_journal
from .lineage import validate_assembly_lineage, validate_assembly_receipt_lineage
from .service import CertifiedAssemblyError, assemble_and_record

__all__ = [
    "ASSEMBLY_SCHEMA_VERSION",
    "INTERVAL_POLICY_ID",
    "WEIGHT_POLICY_ID",
    "AdaptiveAssemblyError",
    "AssemblyContractError",
    "AssemblyJournal",
    "AssemblyJournalError",
    "AssemblyReceipt",
    "CertifiedAssemblyError",
    "assemble_and_record",
    "assemble_prediction",
    "validate_assembly_journal",
    "validate_assembly_lineage",
    "validate_assembly_receipt_lineage",
]
