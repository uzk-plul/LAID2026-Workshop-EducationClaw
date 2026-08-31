# Reflexionsfragen für die Lehre

Dieser Skill erzeugt Reflexionsfragen zu einem frei wählbaren Thema. Die Ausgabe basiert auf einer separaten Markdown-Template-Datei.

## Struktur

```text
hermes-reflexionsfragen-skill/
├── SKILL.md
├── README.md
├── templates/
│   └── reflexionsfragen-template.md
└── references/
    └── beispiel.md
```

## Installation lokal

Lege den Ordner beispielsweise unter einer Hermes-Skill-Kategorie ab:

```bash
mkdir -p ~/.hermes/skills/education
cp -R hermes-reflexionsfragen-skill ~/.hermes/skills/education/reflexionsfragen-lehre
```

Danach kann der Skill über die Hermes-Skill-Verwaltung gefunden werden.

## Beispiel-Prompt

```text
Erstelle 6 Reflexionsfragen zum Thema „Peer Feedback“ für Masterstudierende.
Kontext: Abschluss einer Seminarsitzung.
Reflexionstiefe: hoch.
```

## Anpassung des Templates

Die Datei `templates/reflexionsfragen-template.md` kann unabhängig vom Skill verändert werden. So lassen sich z. B. andere Überschriften, mehr oder weniger Fragen oder zusätzliche Felder wie „Bearbeitungszeit“ ergänzen.
