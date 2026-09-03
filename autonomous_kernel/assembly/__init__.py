"""Evidence-weighted Z8 assembly plus explicit bounded Z9 context overlay.

Z8 remains independently reproducible. Z9 may contextualize an already-valid
Z8 assembly, but neither layer allocates capital, authorizes risk, or executes.
"""

from .adaptive import AdaptiveAssemblyError, assemble_prediction
from .contracts import ASSEMBLY_SCHEMA_VERSION, INTERVAL_POLICY_ID, WEIGHT_POLICY_ID, AssemblyContractError, AssemblyReceipt
from .contextual import CONTEXTUAL_ASSEMBLY_SCHEMA_VERSION, CONTEXT_MULTIPLIER_MAX, CONTEXT_MULTIPLIER_MIN, CONTEXT_WEIGHT_POLICY_ID, ContextualAssemblyError, ContextualAssemblyReceipt, ModelContextProfile, contextualize_prediction
from .contextual_journal import ContextualAssemblyJournal, ContextualAssemblyJournalError, validate_contextual_assembly_journal
from .contextual_lineage import validate_contextual_assembly_lineage, validate_contextual_receipt_lineage
from .contextual_service import CertifiedContextualAssemblyError, contextual_assemble_and_record
from .journal import AssemblyJournal, AssemblyJournalError, validate_assembly_journal
from .lineage import validate_assembly_lineage, validate_assembly_receipt_lineage
from .service import CertifiedAssemblyError, assemble_and_record

__all__ = [
    "ASSEMBLY_SCHEMA_VERSION", "INTERVAL_POLICY_ID", "WEIGHT_POLICY_ID", "AdaptiveAssemblyError", "AssemblyContractError", "AssemblyJournal", "AssemblyJournalError", "AssemblyReceipt", "CertifiedAssemblyError", "assemble_and_record", "assemble_prediction", "validate_assembly_journal", "validate_assembly_lineage", "validate_assembly_receipt_lineage",
    "CONTEXTUAL_ASSEMBLY_SCHEMA_VERSION", "CONTEXT_MULTIPLIER_MAX", "CONTEXT_MULTIPLIER_MIN", "CONTEXT_WEIGHT_POLICY_ID", "ContextualAssemblyError", "ContextualAssemblyJournal", "ContextualAssemblyJournalError", "ContextualAssemblyReceipt", "ModelContextProfile", "CertifiedContextualAssemblyError", "contextual_assemble_and_record", "contextualize_prediction", "validate_contextual_assembly_journal", "validate_contextual_assembly_lineage", "validate_contextual_receipt_lineage",
]
