# OUTPUT, EVIDENCE, DAN AUDIT SCHEMA

Dokumen ini mendefinisikan struktur keluaran wajib agar seluruh proses dapat dipantau, diaudit, dan direproduksi.

## 1. Status machine-readable

Selalu sertakan status berikut di setiap respons orchestrator:
```json
{
  "run_id": "...",
  "stage": "received|validating|simulating|deliberating|report_ready|error",
  "progress": 0,
  "active_agents": [],
  "blocking_issues": [],
  "next_action": "..."
}
```

## 2. Run manifest

```json
{
  "run_id": "...",
  "created_at": "...",
  "model_code_versions": [],
  "data_snapshots": [{"source": "...", "vintage": "...", "hash": "..."}],
  "random_seeds": [],
  "environment": {"os": "...", "runtime": "...", "dependencies": []},
  "reproducibility_instructions": "..."
}
```

## 3. Provenance setiap angka

Setiap nilai numerik wajib membawa provenance:
```json
{
  "value": 0,
  "unit": "...",
  "period": "...",
  "type": "observed|estimated|model_output|assumption|scenario",
  "method": "...",
  "source": {"citation": "...", "url_or_doi": "...", "retrieved_at": "...", "verification": "verified|unverified"},
  "uncertainty": {"low": 0, "high": 0, "distribution": "...", "basis": "..."},
  "as_of": "..."
}
```

## 4. Evidence pack

```json
{
  "evidence_id": "...",
  "claim_ids": [],
  "citation": "...",
  "type": "primary_data|peer_reviewed|working_paper|official_report|model_doc",
  "url_or_doi": "...",
  "retrieved_at": "...",
  "quote": "...",
  "finding": "...",
  "relevance": "...",
  "method_quality": "high|medium|low",
  "limitations": [],
  "applicability_to_scenario": "...",
  "verification_status": "verified|unverified|not_found"
}
```

Aturan: klaim tanpa evidence terverifikasi harus diturunkan menjadi `assumption` atau `dissent`, tidak boleh tampil sebagai fakta.

## 5. Uncertainty registry

```json
{
  "uncertainty_id": "...",
  "type": "parameter|model|structural|data|policy|political|external",
  "description": "...",
  "range_or_distribution": {},
  "sources": [],
  "affected_outputs": [],
  "mitigation_or_treatment": "..."
}
```

Wajib mencakup minimal: parameter uncertainty, model uncertainty (inter-model disagreement), structural uncertainty, data vintage/revisi, dan uncertainty politik/implementasi. Sensitivity dan scenario analysis harus merujuk uncertainty id ini.

## 6. Disagreement register

```json
{
  "disagreement_id": "...",
  "issue": "...",
  "positions": [{"agent_id": "...", "position": "...", "evidence_ids": [], "confidence": "..."}],
  "evidence_conflict": "...",
  "resolution": "resolved|unresolved|escalated_to_decision_maker",
  "decision_criteria": "...",
  "minority_report": "..."
}
```

## 7. Pareto dan alternatif

```json
{
  "alternative_id": "...",
  "description": "...",
  "instrument_settings": {},
  "outcomes": {"growth": {}, "equity": {}, "revenue": {}, "debt": {}, "risk": {}, "feasibility": {}},
  "dominated_by": "alternative_id|null",
  "is_pareto_optimal": true,
  "trade_offs": [],
  "decision_criteria_summary": {}
}
```

Rule: alternatif hanya boleh disebut Pareto-optimal relatif terhadap set kriteria yang dinyatakan eksplisit. Tampilkan frontier dan alternatif yang didominasi.

## 8. Risk register

```json
{
  "risk_id": "...",
  "description": "...",
  "category": "macro|fiscal|distributional|implementation|political|external|legal",
  "probability": {"value": 0, "basis": "..."},
  "impact": {"value": 0, "metric": "..."},
  "severity": "low|medium|high|critical",
  "mitigation": [],
  "residual_risk": "...",
  "interactions": [],
  "trigger_indicators": []
}
```

Wajib mencakup tail risk (low probability, high impact) dan skenario optimistis/pesimistis, bukan hanya baseline.

## 9. Feasibility

```json
{
  "administrative": {"score": 0, "rationale": "...", "requirements": []},
  "legal": {"score": 0, "rationale": "...", "gaps": []},
  "political": {"score": 0, "rationale": "...", "stakeholders": []},
  "capacity": {"score": 0, "rationale": "...", "gaps": []},
  "timeline": {"phases": [], "dependencies": [], "critical_path": "..."},
  "data_requirements": []
}
```

## 10. Audit trail

Setiap aksi menghasilkan entry:
```json
{
  "seq": 0,
  "timestamp": "...",
  "agent_id": "...",
  "action_type": "input|tool_call|simulation|computation|retrieval|verification|deliberation|decision|error",
  "description": "...",
  "inputs": [],
  "outputs": [],
  "assumptions": [],
  "evidence_ids": [],
  "confidence": "...",
  "errors": [],
  "next": "..."
}
```

Audit trail harus append-only, berurutan, dan lengkap dari input sampai rekomendasi. Setiap angka pada laporan final harus dapat dilacak ke audit entry dan evidence pack.

## 11. Final report schema (ringkas)

```json
{
  "run_manifest": {},
  "executive_summary": {"key_finding": "...", "main_recommendation": "...", "confidence": "...", "top_risks": []},
  "scenario": {},
  "baseline_vs_policy": {},
  "methodology": {"models": [], "equations_ref": [], "calibration": [], "data": []},
  "results": {"macro": {}, "fiscal": {}, "distributional": {}, "uncertainty": {}, "sensitivity": {}},
  "deliberation": {"positions": [], "consensus": [], "disagreements": [], "pareto": []},
  "risks": [],
  "feasibility": {},
  "recommendations": {"primary": {}, "alternatives": [], "monitoring": [], "contingency": []},
  "limitations": [],
  "references": [],
  "evidence_pack": [],
  "audit_trail": []
}
```

## 12. Aturan anti-halusinasi

1. Tidak ada referensi, DOI, dataset, institusi, atau kutipan yang tidak dapat diverifikasi.
2. Tidak ada hasil simulasi tanpa eksekusi engine; jika belum jalan, tulis `not_executed`.
3. Tidak ada confidence tinggi untuk klaim tanpa data; gunakan `low` atau tandai `assumption`.
4. Tidak ada generalisasi dari studi lain tanpa justifikasi transferabilitas.
5. Tidak ada nomor telepon, alamat, atau entitas fiktif.
6. Tidak ada ringkasan yang menyembunyikan limitations, disagreement, atau uncertainty.

## 13. Traceability checklist

- setiap rekomendasi → decision criteria → deliberation entry → evidence.
- setiap angka → model/komputasi → input → data snapshot → sumber.
- setiap klaim → claims registry → evidence pack → verification status.
- setiap disagreement → positions → evidence conflict → resolution.
- setiap risiko → trigger → monitoring indicator → contingency.