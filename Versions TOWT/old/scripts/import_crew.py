"""
Import crew members from CSV/inline data.
Run inside Docker: docker exec towt-app-v2 python3 scripts/import_crew.py
"""
import asyncio
from datetime import date

# Role mapping from French labels to DB codes
ROLE_MAP = {
    "Capitaine": "capitaine",
    "Second Capitaine": "second",
    "Chef Mécanicien": "chef_mecanicien",
    "Chef Mecanicien": "chef_mecanicien",
    "Cuisinier": "cook",
    "Lieutenant": "lieutenant",
    "Bosco": "bosco",
    "Matelot": "marin",
    "Graisseur": "marin",
    "Maître d'équipage": "bosco",
    "Maitre d'equipage": "bosco",
    "Élève monovalent": "eleve_officier",
    "Eleve monovalent": "eleve_officier",
    "Élève polyvalent": "eleve_officier",
    "Eleve polyvalent": "eleve_officier",
    "Electro technicien": "chef_mecanicien",
}

def parse_date(s):
    if not s or not s.strip():
        return None
    s = s.strip()
    # Try DD/MM/YYYY
    parts = s.split('/')
    if len(parts) == 3:
        return date(int(parts[2]), int(parts[1]), int(parts[0]))
    return None

# Crew data from the spreadsheet
CREW_DATA = [
    {"first_name": "Leo", "last_name": "ALLAIN", "role": "Second Capitaine", "phone": "06 34 11 72 7", "email": "leo.allain@su", "nationality": "Francaise", "passport_number": "20EA27984", "passport_expiry": "14/10/2030"},
    {"first_name": "Bastien", "last_name": "ARIZZI", "role": "Second Capitaine", "phone": "07 67 76 14 3", "email": "bastien.arizzi@ymail.com", "nationality": "Francaise", "passport_number": "22HC10222", "passport_expiry": "28/09/2032"},
    {"first_name": "Mody", "last_name": "BA", "role": "Graisseur", "nationality": "Senegalaise", "passport_number": "A04596531", "passport_expiry": "21/10/2030"},
    {"first_name": "Anne-Laure", "last_name": "BARBERIS", "role": "Second Capitaine", "phone": "07 67 78 06 7", "email": "annelaure@bareris.info", "nationality": "Francaise", "passport_number": "21EA90539", "passport_expiry": "25/11/2031"},
    {"first_name": "Bruno", "last_name": "BAZIN", "role": "Eleve monovalent", "phone": "06 63 32 17 6", "email": "brbazin@gmail.com", "nationality": "Francaise", "passport_number": "18FF85668", "passport_expiry": "14/10/2028"},
    {"first_name": "Jean-Marc", "last_name": "BERNARD", "role": "Chef Mecanicien", "phone": "06 17 58 45 5", "email": "kamalij3@gmail.com", "nationality": "Francaise", "passport_number": "25AA49433", "passport_expiry": "14/01/2035"},
    {"first_name": "Kassi Andre", "last_name": "BEUGRE", "role": "Cuisinier", "nationality": "Ivoirienne", "passport_number": "22AI17389", "passport_expiry": "18/10/2027"},
    {"first_name": "Mael", "last_name": "BOIS", "role": "Matelot", "phone": "06 51 47 29 0", "email": "maelbois.mb@gmail.com", "nationality": "Francaise", "passport_number": "23CC63988", "passport_expiry": "09/02/2033"},
    {"first_name": "Pierrick", "last_name": "BOUCHER", "role": "Capitaine", "phone": "06 89 04 99 6", "email": "pierrick.boucher@hotmail.fr", "nationality": "Francaise", "passport_number": "19FV16176", "passport_expiry": "26/11/2029"},
    {"first_name": "Ousseynou", "last_name": "BOUSSO", "role": "Chef Mecanicien", "phone": "06 34 10 98 0", "email": "obousso@yahoo.fr", "nationality": "Senegalaise", "passport_number": "A02916306", "passport_expiry": "25/08/2026"},
    {"first_name": "Matthieu", "last_name": "BRILLAC", "role": "Lieutenant", "phone": "07 83 37 66 7", "email": "matthieu.brillac@supmaritime.fr", "nationality": "Francaise", "passport_number": "18FK20141", "passport_expiry": "08/11/2028"},
    {"first_name": "Hadrien", "last_name": "BUSSON", "role": "Capitaine", "phone": "06 33 67 98 2", "email": "busson.hadrien@free.fr", "nationality": "Francaise", "passport_number": "20CC30341", "passport_expiry": "02/03/2030"},
    {"first_name": "Delormasse Kevin", "last_name": "CHAWA", "role": "Cuisinier", "nationality": "Ivoirienne", "passport_number": "20AF23741", "passport_expiry": "29/12/2026"},
    {"first_name": "Helene", "last_name": "DECOCK", "role": "Maitre d'equipage", "phone": "06 48 30 33 0", "email": "decock.ln@gmail.com", "nationality": "Francaise", "passport_number": "25AI16203", "passport_expiry": "17/02/2035"},
    {"first_name": "Kevin", "last_name": "DELANNOY", "role": "Capitaine", "phone": "06 03 19 29 7", "email": "kevin.delannoy.kd@gmail.com", "nationality": "Francaise", "passport_number": "25AI58052", "passport_expiry": "19/02/2035"},
    {"first_name": "Alexandre", "last_name": "DESROYS", "role": "Chef Mecanicien", "phone": "06 33 92 97 3", "email": "a.desroys@gmail.com", "nationality": "Francaise", "passport_number": "16CE25484", "passport_expiry": "29/09/2016", "is_foreign": False},
    {"first_name": "El Hadji Badra", "last_name": "DIAKITE", "role": "Matelot", "nationality": "Ivoirienne", "passport_number": "23AL16146", "passport_expiry": "23/07/2028"},
    {"first_name": "Florian", "last_name": "FOURCIER", "role": "Maitre d'equipage", "phone": "06 37 86 89 9", "email": "florian.fourcier@lilo.org", "nationality": "Francaise", "passport_number": "24HI26225", "passport_expiry": "30/09/2034"},
    {"first_name": "Marine", "last_name": "FRECER", "role": "Matelot", "phone": "07 52 06 85 8", "email": "frecer-marine@hotmail.com", "nationality": "Francaise", "passport_number": "18FK31826", "passport_expiry": "11/11/2028"},
    {"first_name": "Mamadou Lamine", "last_name": "GAYE", "role": "Chef Mecanicien", "phone": "07 44 82 73 4", "email": "laminosgayos@hotmail.com", "nationality": "Senegalaise", "passport_number": "A03073244", "passport_expiry": "22/03/2027"},
    {"first_name": "Kouame Toussaint", "last_name": "KOUAKOU", "role": "Matelot", "nationality": "Ivoirienne", "passport_number": "25AA18715", "passport_expiry": "01/06/2030"},
    {"first_name": "Gwenola", "last_name": "LE GUIL", "role": "Capitaine", "phone": "06 84 01 90 8", "email": "gwen.leguil@orange.fr", "nationality": "Francaise", "passport_number": "18HC41953", "passport_expiry": "03/12/2028"},
    {"first_name": "Philippe", "last_name": "LE HEN", "role": "Eleve monovalent", "phone": "06 89 84 82 8", "email": "philippe.lehen@gmail.com", "nationality": "Francaise", "passport_number": "23KH22786", "passport_expiry": "18/10/2033"},
    {"first_name": "Sebastien", "last_name": "LE QUEAU", "role": "Chef Mecanicien", "phone": "07 71 74 19 8", "email": "sebastien.le.queau@gmail.com", "nationality": "Francaise", "passport_number": "18EI11399", "passport_expiry": "19/08/2028"},
    {"first_name": "Agathe", "last_name": "LECOMTE", "role": "Lieutenant", "phone": "06 75 40 27 7", "email": "agathe_lecomte@outlook.com", "nationality": "Francaise", "passport_number": "17EH38268", "passport_expiry": "23/10/2027"},
    {"first_name": "Martin", "last_name": "LEGAUD", "role": "Eleve polyvalent", "phone": "07 44 77 27 6", "email": "martin.legaud@supmaritime.fr", "nationality": "Francaise", "passport_number": "23IE66697", "passport_expiry": "05/09/2033"},
    {"first_name": "Lucie", "last_name": "LEGENDRE", "role": "Maitre d'equipage", "phone": "06 61 51 28 4", "email": "lucielegendre@ymail.com", "nationality": "Francaise", "passport_number": "24IC88257", "passport_expiry": "24/10/2034"},
    {"first_name": "Pierre-Antoine", "last_name": "LIZEE", "role": "Capitaine", "phone": "06 89 18 25 9", "email": "rhumb@hotmail.fr", "nationality": "Francaise", "passport_number": "25HK17507", "passport_expiry": "27/11/2035"},
    {"first_name": "Jerome", "last_name": "LOQUIN", "role": "Second Capitaine", "phone": "06 27 22 76 1", "email": "jloquin@hotmail.fr", "nationality": "Francaise", "passport_number": "23CE57571", "passport_expiry": "21/02/2033"},
    {"first_name": "Remi", "last_name": "MALOU", "role": "Graisseur", "nationality": "Senegalaise", "passport_number": "A03747162", "passport_expiry": "02/01/2029"},
    {"first_name": "Nathan", "last_name": "MASSON", "role": "Eleve monovalent", "phone": "07 67 79 58 9", "email": "nathan.masson@supmaritime.fr", "nationality": "Francaise", "passport_number": "23KK53225", "passport_expiry": "30/10/2033"},
    {"first_name": "Frederic", "last_name": "MAURY", "role": "Electro technicien", "phone": "06 62 68 21 3", "email": "frdmaury@yahoo.fr", "nationality": "Francaise", "passport_number": "19KP98110", "passport_expiry": "06/06/2029"},
    {"first_name": "Adam", "last_name": "MIKIELSKI", "role": "Eleve polyvalent", "phone": "07 85 86 66 8", "email": "adam.mikielski@hotmail.com", "nationality": "Francaise", "passport_number": "25H140778", "passport_expiry": "24/11/2035"},
    {"first_name": "Camille", "last_name": "MURIGNEUX", "role": "Matelot", "phone": "06 44 35 79 5", "email": "camurigneux@gmail.com", "nationality": "Francaise", "passport_number": "20DD22084", "passport_expiry": "15/07/2030"},
    {"first_name": "Charles Adoubi", "last_name": "NDIA", "role": "Cuisinier", "nationality": "Ivoirienne", "passport_number": "23AR02652", "passport_expiry": "18/04/2029"},
    {"first_name": "Ephraim Jules", "last_name": "NTSUEGO", "role": "Matelot", "nationality": "Ghaneenne", "passport_number": "G3829328", "passport_expiry": "25/10/2032"},
    {"first_name": "Ngolo", "last_name": "OUATTARA", "role": "Matelot", "nationality": "Ivoirienne", "passport_number": "23AL58528", "passport_expiry": "19/09/2028"},
    {"first_name": "Renaud", "last_name": "PAUL", "role": "Second Capitaine", "phone": "06 09 94 76 6", "email": "renaud.paul@supmaritime.fr", "nationality": "Francaise", "passport_number": "18AI16517", "passport_expiry": "12/02/2028"},
    {"first_name": "Antonin", "last_name": "PETIT", "role": "Capitaine", "phone": "06 25 01 04 0", "email": "antoninpetit@me.com", "nationality": "Francaise", "passport_number": "18AP22087", "passport_expiry": "17/12/2028"},
    {"first_name": "Simon", "last_name": "ROSSI", "role": "Lieutenant", "phone": "06 22 16 08 2", "email": "simon.rossi@hydros-alumni.fr", "nationality": "Francaise", "passport_number": "25ED51072", "passport_expiry": "29/06/2035"},
    {"first_name": "Camille", "last_name": "ROUBINOWITZ", "role": "Maitre d'equipage", "phone": "06 17 99 52 6", "email": "camille.roubi@gmail.com", "nationality": "Francaise", "passport_number": "17AK56895", "passport_expiry": "02/03/2027"},
    {"first_name": "Djiby", "last_name": "SARR", "role": "Chef Mecanicien", "nationality": "Senegalaise", "passport_number": "A04531216", "passport_expiry": "18/09/2030"},
    {"first_name": "Quentin", "last_name": "SCHMELTZER", "role": "Eleve monovalent", "phone": "06 30 06 45 5", "email": "schmeltzer.quentin@gmail.com", "nationality": "Francaise", "passport_number": "19AP40109", "passport_expiry": "14/04/2029"},
    {"first_name": "Maya Desire", "last_name": "YOUAN", "role": "Graisseur", "nationality": "Ivoirienne", "passport_number": "25AA24812", "passport_expiry": "12/06/2030"},
]


async def main():
    from app.database import engine, async_session
    from app.models.crew import CrewMember
    from sqlalchemy import select

    async with async_session() as db:
        inserted = 0
        skipped = 0
        updated = 0

        for row in CREW_DATA:
            role_code = ROLE_MAP.get(row["role"], "marin")
            passport_exp = parse_date(row.get("passport_expiry"))
            is_foreign = row.get("nationality", "Francaise") != "Francaise"

            # Check if member already exists (by last_name + first_name)
            result = await db.execute(
                select(CrewMember).where(
                    CrewMember.last_name == row["last_name"],
                    CrewMember.first_name == row["first_name"],
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                # Update missing fields
                changed = False
                if not existing.passport_number and row.get("passport_number"):
                    existing.passport_number = row["passport_number"]
                    changed = True
                if not existing.passport_expiry and passport_exp:
                    existing.passport_expiry = passport_exp
                    changed = True
                if not existing.nationality and row.get("nationality"):
                    existing.nationality = row["nationality"]
                    changed = True
                if not existing.phone and row.get("phone"):
                    existing.phone = row.get("phone", "").replace(" ", "")
                    changed = True
                if not existing.email and row.get("email"):
                    existing.email = row.get("email")
                    changed = True
                existing.is_foreign = is_foreign
                if changed:
                    updated += 1
                    print(f"  Updated: {row['first_name']} {row['last_name']}")
                else:
                    skipped += 1
                continue

            member = CrewMember(
                first_name=row["first_name"],
                last_name=row["last_name"],
                role=role_code,
                phone=row.get("phone", "").replace(" ", "") if row.get("phone") else None,
                email=row.get("email"),
                is_active=True,
                is_foreign=is_foreign,
                nationality=row.get("nationality"),
                passport_number=row.get("passport_number"),
                passport_expiry=passport_exp,
            )
            db.add(member)
            inserted += 1
            print(f"  Inserted: {row['first_name']} {row['last_name']} ({role_code})")

        await db.commit()
        print(f"\nDone: {inserted} inserted, {updated} updated, {skipped} skipped (unchanged)")


if __name__ == "__main__":
    asyncio.run(main())
