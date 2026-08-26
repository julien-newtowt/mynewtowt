"""Booking note contractuelle établie à la validation d'une offre.

Lot 5 de la refonte commerciale.

⚠️ **Homonymie levée.** « Booking note » désignait déjà, dans l'espace client, le
PDF de *confirmation de réservation* (``/me/bookings/{ref}/booking-note.pdf``),
terme installé dans les cinq catalogues de traduction. Ce n'est pas le même
document : celui-ci est le **contrat de réservation d'espace en cale** sur trame
de type BIMCO CONLINEBOOKING, signé par le chargeur et le transporteur. Le nom
« booking note » revient au document contractuel ; la confirmation client est
renommée « confirmation de réservation » côté interface.

Les champs préremplis sont **stockés** plutôt que rendus à la volée : le
commercial les corrige avant diffusion, et ce qui a été envoyé au client doit
rester consultable tel quel même si l'offre ou le référentiel évoluent ensuite.

Les colonnes de signature électronique sont posées dès maintenant bien que le
lot YouSign suive : les ajouter séparément imposerait une seconde migration sur
la même table pour un gain nul.

Aucune reprise : les offres déjà validées avant ce lot n'ont pas de booking note
et n'en recevront pas rétroactivement — en fabriquer une antidaterait un contrat.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260826_0123"
down_revision = "20260826_0122"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "booking_notes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("offer_id", sa.Integer(), nullable=False),
        sa.Column("reference", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="brouillon"),
        # Champs du formulaire, préremplis et modifiables avant diffusion.
        sa.Column("issue_place", sa.String(length=120), nullable=True),
        sa.Column("issued_on", sa.DateTime(timezone=True), nullable=True),
        sa.Column("agents_pod", sa.Text(), nullable=True),
        sa.Column("vessel_name", sa.String(length=120), nullable=True),
        sa.Column("time_for_shipment", sa.String(length=120), nullable=True),
        sa.Column("pol_text", sa.String(length=200), nullable=True),
        sa.Column("pod_text", sa.String(length=200), nullable=True),
        sa.Column("merchant_name", sa.String(length=200), nullable=True),
        sa.Column("merchant_contact", sa.String(length=200), nullable=True),
        sa.Column("merchant_address", sa.Text(), nullable=True),
        sa.Column("merchant_email", sa.String(length=200), nullable=True),
        sa.Column("freight_terms", sa.Text(), nullable=True),
        sa.Column("payment_terms", sa.Text(), nullable=True),
        sa.Column("special_terms", sa.Text(), nullable=True),
        sa.Column("cargo_description", sa.Text(), nullable=True),
        # Diffusion.
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_by_id", sa.Integer(), nullable=True),
        sa.Column("issued_by_name", sa.String(length=200), nullable=True),
        sa.Column("document_sha256", sa.String(length=64), nullable=True),
        # Signature électronique (lot suivant).
        sa.Column("signature_provider", sa.String(length=40), nullable=True),
        sa.Column("signature_request_id", sa.String(length=120), nullable=True),
        sa.Column("signature_status", sa.String(length=40), nullable=True),
        sa.Column("signature_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signed_document_path", sa.String(length=500), nullable=True),
        sa.Column("signed_document_sha256", sa.String(length=64), nullable=True),
        sa.Column("auto_generated", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["offer_id"], ["rate_offers.id"], ondelete="CASCADE"),
        # Une offre validée donne lieu à **une seule** booking note : revalider
        # ne doit pas pouvoir fabriquer un second contrat.
        sa.UniqueConstraint("offer_id", name="uq_booking_note_offer"),
        sa.UniqueConstraint("reference", name="uq_booking_note_reference"),
    )
    op.create_index("ix_booking_notes_offer_id", "booking_notes", ["offer_id"])
    op.create_index(
        "ix_booking_notes_signature_request_id", "booking_notes", ["signature_request_id"]
    )


def downgrade():
    op.drop_index("ix_booking_notes_signature_request_id", table_name="booking_notes")
    op.drop_index("ix_booking_notes_offer_id", table_name="booking_notes")
    op.drop_table("booking_notes")
