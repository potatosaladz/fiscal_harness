# Fiscal Astra Project Development Rules

## General

- Baca seluruh dokumentasi project sebelum mengubah kode.
- Jangan mengarang data, hasil simulasi, referensi, DOI, URL, atau hasil numerical computation.
- Jangan mengklaim simulation engine telah berjalan jika belum dieksekusi.
- Bedakan observed data, estimated value, model output, assumption, scenario, dan claim.
- Semua angka wajib memiliki unit, periode, provenance, metode, dan uncertainty.
- Semua perubahan harus disertai test yang relevan.
- Jangan menambahkan dependency tanpa alasan teknis.
- Jangan menghapus audit trail ketika terjadi error.

## Simulation

- Local LLM hanya merencanakan, mengorkestrasi, menafsirkan, dan menyusun laporan.
- Perhitungan numerik wajib dilakukan oleh simulation engine.
- Setiap simulation run wajib menyimpan seed, model version, code version, data snapshot, parameter set, dan solver diagnostics.
- Output yang belum benar-benar dihitung harus berstatus `not_executed`.
- Baseline dan policy counterfactual harus dapat dibandingkan.

## Audit

- Audit log harus append-only.
- Setiap agent action, tool call, data retrieval, computation, decision, dan error harus dicatat.
- Setiap recommendation harus dapat ditelusuri ke evidence, simulation result, dan decision criteria.
- Jangan menyembunyikan disagreement, uncertainty, limitation, atau minority position.

## Workflow

- Gunakan Plan mode untuk analisis dan desain.
- Gunakan Act mode untuk implementasi.
- Implementasikan secara bertahap.
- Jalankan formatter, linter, type checking, unit test, dan integration test setelah perubahan.
