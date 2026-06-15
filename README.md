# YachAI

### Bilingual/Trilingual AI Tutor for Rural Andean Students

**Hackathon:** Agents League @ AI Skills Fest 2026
**Track:** Reasoning Agents
**Category:** Hack for Good

---

## The Problem

In rural Andean communities of Peru — especially in regions like Cajabamba, where the NGO "Ponle zapatillas a tus sueños" operates — thousands of students lack access to tutors or quality educational support. Many of these students are native Quechua speakers, learning in Spanish, with little to no help when they struggle with schoolwork.

Most AI tutoring tools simply give answers. This creates a generation of students who copy solutions without understanding the reasoning behind them — and most tools don't speak the languages these communities actually use.

## The Solution: YachAI

YachAI ("Yachaq" = "the one who knows" in Quechua) is an AI agent built on Microsoft Foundry that:

- Communicates in Spanish, English, and Quechua — meeting students where they are linguistically
- Teaches, not just answers — solves Math and Science problems step-by-step, explaining the reasoning behind each step
- Verifies understanding — asks follow-up questions after every explanation, and re-explains differently if the student gets it wrong
- Aligned to Peru's national curriculum — uses the official MINEDU curriculum as a knowledge base via RAG
- Remembers each student — uses Foundry's Memory feature to track recurring mistakes, mastered topics, and language preference over time, personalizing future explanations

## Architecture

- Platform: Microsoft Foundry (no-code Agent Builder)
- Model: o4-mini (reasoning model, chosen for multi-step explanations and Quechua handling)
- Knowledge base: MINEDU National Curriculum (PDF, RAG via vector index)
- Memory: Foundry Memory Store — tracks student profile, learning gaps, and language preference
- Channels: Web app preview / Teams

```
┌─────────────────────────────────────────────┐
│                  Student                     │
│        (Spanish / English / Quechua)         │
└───────────────────┬───────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────┐
│              YachAI (yachaq-01)               │
│              Microsoft Foundry Agent          │
│                Model: o4-mini                 │
├─────────────────────────────────────────────┤
│  Instructions: Step-by-step teaching,         │
│  language detection, verification questions  │
├───────────────────┬───────────────────────────┤
│   Knowledge (RAG)  │      Memory Store         │
│  MINEDU Curriculum │  Student profile, gaps,   │
│      (PDF)         │   language preference     │
└─────────────────────────────────────────────┘
```

## Why It Matters

YachAI was created by a 13-year-old student from Trujillo, Peru, co-founder of "Ponle zapatillas a tus sueños" — an NGO that donates shoes to children in remote areas of Cajabamba. YachAI extends that mission: instead of just material support, it brings personalized, culturally-relevant education to communities that have historically been left behind by technology.

No mainstream AI tutor speaks Quechua. YachAI does.

## Demo Video

https://youtu.be/G6w1B2KJdI8

## Team

- Italo Benites - Montessori International College, Trujillo, Peru

---

Built with Microsoft Foundry for the Agents League Hackathon @ AI Skills Fest 2026
