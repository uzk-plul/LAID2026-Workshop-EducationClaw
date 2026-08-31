---
name: reflexionsfragen
description: Generiert, insbesondere für die Hochschullehre, Reflexionsfragen zu einem gegebenen Thema auf Basis eines einfachen Markdown-Templates.
version: 1.0.0
metadata:
  hermes:
    tags: [education, teaching, learning, deutsch]
    category: educationclaw
---

# Reflexionsfragen für die Lehre

## Wann wird dieser Skill genutzt?

Nutze diesen Skill, wenn eine Lehrperson Reflexionsfragen zu einem Thema erstellen möchte, zum Beispiel für:

- Seminar- oder Vorlesungsabschluss
- Lernjournale und Portfolios
- Gruppenreflexionen
- Vor- oder Nachbereitung einer Lerneinheit
- Transferaufgaben
- Selbstreflexion nach Übungen, Projekten oder Fallarbei
Typische Anfragen sind etwa:

- „Erstelle Reflexionsfragen zum Thema wissenschaftliches Arbeiten.“
- „Gib mir fünf Reflexionsfragen für Studierende nach einer Einheit zu KI-Ethik.“
- „Formuliere Reflexionsfragen zur Gruppenarbeit für eine Berufsschulklasse.“

## Input

Der Skill benötigt mindestens:

- **Thema**: Inhalt oder Gegenstand, zu dem reflektiert werden soll.

Wenn vorhanden, berücksichtige zusätzlich:

- **Zielgruppe**: z. B. Bachelorstudierende, Masterstudierende, Schüler:innen, Weiterbildungsteilnehmende.
- **Lernziel**: Was sollen die Lernenden verstehen, beurteilen oder auf ihre Praxis übertragen?
- **Kontext**: z. B. Seminarabschluss, Lernjournal, Kleingruppe, Einzelreflexion.
- **Anzahl**: gewünschte Zahl der Fragen.
- **Reflexionstiefe**: niedrig, mittel oder hoch.

Wenn optionale Angaben fehlen, frage nicht zwingend nach. Nutze sinnvolle Defaults:

- Zielgruppe: Lernende in der Hochschullehre
- Kontext: individuelle Reflexion nach einer Lerneinheit
- Anzahl: 6 Fragen
- Reflexionstiefe: mittel

## Template

Verwende als Ausgangspunkt immer die Datei:

`templates/reflexionsfragen-template.md`

Behalte deren Grundstruktur bei. Passe Überschriften oder die Zahl der Abschnitte nur an, wenn die Nutzeranfrage dies erfordert.

## Vorgehen

1. **Thema erfassen**
   - Identifiziere das zentrale Thema und gegebenenfalls Teilaspekte.
   - Übernimm Zielgruppe, Lernziel, Kontext, Anzahl und Reflexionstiefe, sofern genannt.

2. **Template laden**
   - Orientiere dich an `templates/reflexionsfragen-template.md`.
   - Ersetze die Platzhalter durch konkrete Angaben.

3. **Fragen didaktisch staffeln**
   Erzeuge eine ausgewogene Mischung aus drei Ebenen:

   - **Wahrnehmen & Verstehen**: eigene Erkenntnisse, Irritationen oder zentrale Gedanken benennen.
   - **Analysieren & Beurteilen**: Annahmen prüfen, Zusammenhänge herstellen, Perspektiven vergleichen.
   - **Transfer & Weiterlernen**: Konsequenzen für Praxis, weiteres Lernen oder zukünftiges Handeln ableiten.

   Bei 6 Fragen ist die Standardverteilung 2 / 2 / 2. Bei anderen Anzahlen verteile möglichst ausgewogen.

4. **Qualitätsregeln anwenden**
   Jede Reflexionsfrage soll:

   - offen formuliert sein und keine reine Ja/Nein-Antwort nahelegen;
   - einen klaren Bezug zum Thema haben;
   - zur Begründung, Konkretisierung oder Perspektivübernahme anregen;
   - sprachlich zur Zielgruppe passen;
   - möglichst nur einen Hauptgedanken pro Frage enthalten;
   - Reflexion statt bloßer Wissensabfrage fördern.

5. **Ton und Schwierigkeit anpassen**
   - **niedrig**: konkret, erfahrungsnah, leicht zugänglich.
   - **mittel**: analytisch, begründend, mit ersten Transferanteilen.
   - **hoch**: metakognitiv, multiperspektivisch, mit Spannungsfeldern und Konsequenzen.

6. **Ausgabe erzeugen**
   - Gib die ausgefüllte Template-Struktur aus.
   - Formuliere nur die fertigen Reflexionsfragen; erkläre den Generierungsprozess nicht, außer dies wird ausdrücklich gewünscht.

## Qualitätsregeln

- Vermeide suggestive Fragen, die eine „richtige“ Haltung voraussetzen.
- Vermeide Prüfungsfragen wie „Definieren Sie …“ oder „Nennen Sie drei …“, sofern nicht ausdrücklich gewünscht.
- Vermeide unnötig persönliche oder intime Fragen. Reflexion darf auf Lernprozess, Entscheidungen, Perspektiven und Anwendung fokussieren.
- Bei kontroversen Themen formuliere multiperspektivisch und neutral.
- Bei sensiblen Themen ermögliche Distanz, z. B. durch Bezug auf Fallbeispiele oder professionelle Praxis statt auf persönliche Erfahrungen.
- Wiederhole denselben Fragetyp nicht mehrfach nur mit anderen Worten.

## Output-Format

Nutze standardmäßig die im Template (`reflexionsfragen-template.md`) vorgegebene Struktur.

Wenn die Nutzerin oder der Nutzer ausdrücklich nur eine Fragenliste möchte, lasse die Metadaten und Abschnittsüberschriften weg, behalte aber die inhaltliche Staffelung intern bei.

Alle Fragen sind immer auf Deutsch.

## Verifikation

Prüfe vor der Ausgabe:

1. Sind es genau so viele Fragen wie gewünscht?
2. Bezieht sich jede Frage erkennbar auf das Thema?
3. Sind die Fragen überwiegend offen und reflexiv statt wissensprüfend?
4. Sind mindestens zwei Reflexionsebenen vertreten; bei 5 oder mehr Fragen möglichst alle drei?
5. Gibt es keine unnötigen Doppelungen?
6. Passen Sprache und Tiefe zur Zielgruppe?
