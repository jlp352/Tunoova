# Charte graphique — Tunoova

> Référence de design pour le site commercial, cohérente avec l'application.
> Les couleurs viennent de `app/static/css/app.css` et du logo (`app/static/brand/`, `Doc/logo/`).

---

## 1. Esprit & ton

- **Univers** : musique, radios et podcasts chez soi, pour toute la famille
- **Mode** : clair par défaut, avec un mode sombre (comme l'application)
- **Ambiance** : chaleureuse, ludique, rassurante. Des formes rondes, des couleurs franches mais douces
- **Principe clé** : le **violet** porte la marque, l'**orange** porte l'action, et les quatre couleurs du logo servent d'accents. Elles ne servent jamais de fond pour de grandes surfaces

---

## 2. Palette de couleurs

### Couleurs de marque

| Nom | Variable CSS | Valeur | Usage |
|---|---|---|---|
| Violet Tunoova | `--violet` | `#5C4B87` | Fond du logo, header, nav, liens (aussi `theme-color` de la PWA) |
| Violet profond | `--violet-dark` | `#453868` | Survol, footer |
| Crème | `--creme` | `#F5E9D3` | Lettrage du logo, titres sur violet |
| Lavande | `--lavande` | `#EDE9F5` | Fonds teintés, badges, encadrés |

### Couleurs d'accent (anneaux du logo, mèches de la mascotte)

| Nom | Variable CSS | Valeur | Usage |
|---|---|---|---|
| Orange | `--orange` | `#E8632A` | Boutons d'action (CTA) |
| Orange foncé | `--orange-dark` | `#C9501C` | Survol du CTA, bouton orange en petite taille (contraste AA) |
| Jaune | `--jaune` | `#F2B33D` | Mise en valeur, mots-clés sur violet |
| Vert | `--vert` | `#3E9B5C` | Validation, coches, « inclus » |
| Bleu | `--bleu` | `#2D6E9E` | Info, liens secondaires, icônes |

### Neutres (thème clair)

| Nom | Variable CSS | Valeur |
|---|---|---|
| Fond | `--bg` | `#FAFAFC` |
| Surface | `--surface` | `#FFFFFF` |
| Texte | `--text` | `#1F1B2E` |
| Texte discret | `--text-muted` | `#6B6478` |
| Bordure | `--border` | `#E5E1EC` |

### Nuances de violet

| Nuance | Valeur | Usage dans l'app |
|---|---|---|
| Violet profond | `#453868` | Survol des boutons, texte sur lavande |
| Violet Tunoova | `#5C4B87` | Couleur de marque |
| Violet moyen | `#7A64B8` | Fin de dégradé des tuiles |
| Violet clair | `#8A7CC4` | Marque en mode sombre |
| Mauve | `#9C8AD6` | Badges, survol des bordures |
| Glycine | `#C9BEF0` | Curseurs sur fond violet |
| Lavande | `#EDE9F5` | Messages flash, lignes de groupe |

### Couleurs fonctionnelles

| Contexte | Valeur |
|---|---|
| Succès / inclus | `#3E9B5C` (vert du logo) |
| Erreur | `#B3261E` |
| Pastille de notification | `#E3342F` |
| Avertissement | `#F2B33D` (jaune du logo) |
| Information | `#2D6E9E` (bleu du logo) |

### Dégradés signatures

```css
/* Arc-en-ciel (mèches de la mascotte) : soulignements, barres fines */
linear-gradient(90deg, #E8632A, #F2B33D, #3E9B5C, #2D6E9E)

/* Surfaces violettes : tuiles, bandeaux */
linear-gradient(135deg, #5C4B87, #7A64B8)

/* Hero, footer */
linear-gradient(135deg, #453868, #5C4B87)
```

> Règle : l'arc-en-ciel s'utilise en **traits fins** (soulignement, séparateur, barre de progression). On ne l'applique ni au texte ni à de grandes surfaces.

```css
.rainbow-line {
    height: 5px;
    width: 90px;
    border-radius: 999px;
    background: linear-gradient(90deg, #E8632A, #F2B33D, #3E9B5C, #2D6E9E);
}
```

### Contrastes (accessibilité)

| Combinaison | Statut |
|---|---|
| Blanc / crème sur violet `#5C4B87` | 7,5 / 6,2 : 1 — ✓ tout texte |
| Texte `#1F1B2E` sur fond clair | 16 : 1 — ✓ tout texte |
| Blanc sur orange `#E8632A` | 3,4 : 1 — ✓ texte large uniquement (gras ≥ 19px). Pour un bouton plus petit, utiliser l’orange foncé `#C9501C` (4,5 : 1) |
| Jaune sur violet | 4 : 1 — ✓ titres uniquement |
| Blanc sur jaune, ou jaune sur blanc | 1,9 : 1 — ✗ à éviter |

---

## 3. Typographie

### Polices

| Rôle | Famille | Weights | Source |
|---|---|---|---|
| Titres | `Fredoka` | 400, 500, 600, 700 | Google Fonts |
| Corps / UI | `Nunito` | 400, 600, 700, 800 | Google Fonts |

```html
<link href="https://fonts.googleapis.com/css2?family=Fredoka:wght@400;500;600;700&family=Nunito:wght@400;600;700;800&display=swap" rel="stylesheet">
```

> Fredoka est ronde et épaisse, proche du lettrage du logo. L'application utilise la police système du téléphone. Le site commercial peut se permettre une identité plus marquée.

### Règles d'usage

- **Fredoka** → titres hero, titres de section, titres de cards, prix.
- **Nunito** → tout le reste : navigation, corps, boutons, badges, footer.
- Pas de majuscules forcées ni de `letter-spacing` large : le ton reste amical, pas « affiche de concert ».

### Échelle typographique

| Élément | Font | Taille | Poids |
|---|---|---|---|
| Hero H1 | Fredoka | `clamp(2.5rem, 6vw, 4.5rem)`, lh 1.05 | 700 |
| Titre de section | Fredoka | `clamp(2rem, 4.5vw, 2.8rem)` | 600 |
| Titre de card | Fredoka | `1.35rem` | 600 |
| Prix | Fredoka | `2.8rem` | 700 |
| Navigation | Nunito | `0.95rem` | 700 |
| Corps | Nunito | `1.1rem`, lh 1.7 | 400 |
| Bouton | Nunito | `0.95–1.05rem` | 700–800 |
| Badge | Nunito | `0.78rem` | 800 |
| Footer / mentions | Nunito | `0.85rem`, `--text-muted` | 400 |

---

## 4. Logo & mascotte

- Fichiers : `Doc/logo/tunoova-wordmark-violet.png`, `Doc/logo/tunoova-wordmark-transparent.png`, `Doc/logo/tunoova-logo-complet.png`, `Doc/logo/mascotte-*.png`, `app/static/brand/app-icon-512.png`
- Version principale : wordmark **crème sur violet** `#5C4B87`
- Motif signature : les deux « o » en forme d'**anneaux** (orange/jaune et vert/bleu), réutilisables seuls comme élément décoratif

| À faire | À éviter |
|---|---|
| Wordmark crème sur violet | Wordmark sur fond clair sans son cartouche violet |
| Marge libre autour du logo ≥ hauteur d'un « o » | Déformer, recolorer ou ajouter une ombre portée |
| Hauteur minimale de 24px à l'écran | Mettre le logo sur une photo chargée |

### Anneaux en CSS pur

```css
.ring {
    width: 72px; height: 72px;
    border-radius: 50%;
    background: radial-gradient(circle closest-side,
        #5C4B87 0 28%, #F2B33D 28% 52%, #5C4B87 52% 62%, #E8632A 62% 100%);
}
.ring.cool {   /* second "o" du logo */
    background: radial-gradient(circle closest-side,
        #5C4B87 0 28%, #2D6E9E 28% 52%, #5C4B87 52% 62%, #3E9B5C 62% 100%);
}
```

---

## 5. Ombres

Les ombres sont douces et légèrement teintées de violet, sans effet néon. Seul le bouton d'action a une ombre orange.

```css
/* Card repos */      box-shadow: 0 4px 20px rgba(31, 27, 46, 0.06);
/* Card survol */     box-shadow: 0 16px 40px rgba(92, 75, 135, 0.18);
/* Header sticky */   box-shadow: 0 10px 30px rgba(92, 75, 135, 0.25);
/* Bouton CTA */      box-shadow: 0 8px 22px rgba(232, 99, 42, 0.35);
/* CTA survol */      box-shadow: 0 12px 28px rgba(232, 99, 42, 0.45);
/* Modale */          box-shadow: 0 8px 30px rgba(0, 0, 0, 0.25);
/* Focus input */     box-shadow: 0 0 0 4px rgba(92, 75, 135, 0.15);
```

---

## 6. Bordures & rayons

| Élément | `border-radius` |
|---|---|
| Input (app), vignette | `8px` |
| Input (site), petite card | `12px` |
| Bloc, pochette | `16px` |
| Card, hero | `20px` |
| Boutons, badges | `999px` (pilule) |

**Bordures :** `1px solid #E5E1EC` pour une séparation ou une card au repos, `2px solid #5C4B87` pour un élément actif, un focus ou un bouton outline.

---

## 7. Layout & espacements

| Propriété | Valeur |
|---|---|
| Largeur max du contenu | `1200px` |
| Padding des sections | `5rem 1.5rem` (`3rem` sur mobile) |
| Gap de grille (cards) | `1.5rem` |
| Colonne minimum card | `280px` |
| Grille | `repeat(auto-fit, minmax(280px, 1fr))` |
| Échelle d'espacement | multiples de 4px : 4, 8, 12, 16, 24, 32, 48, 80 |

---

## 8. Composants

### Navigation

```css
header {
    background: #5C4B87;
    box-shadow: 0 10px 30px rgba(92, 75, 135, 0.25);
    padding: 1rem 1.5rem;
}
nav a {
    color: #fff;
    font-family: 'Nunito', sans-serif;
    font-weight: 700;
    font-size: 0.95rem;
    text-decoration: none;
    position: relative;
    transition: color 0.25s;
}
nav a::after {
    content: '';
    position: absolute;
    bottom: -6px; left: 0;
    width: 0; height: 3px;
    border-radius: 999px;
    background: #F2B33D;
    transition: width 0.25s;
}
nav a:hover { color: #F5E9D3; }
nav a:hover::after { width: 100%; }
```

### Hero

```html
<section class="hero">
    <div class="ring deco"></div>
    <h1>Votre musique,<br><em>votre serveur.</em></h1>
    <p>MP3, radios et podcasts réunis dans une seule application, installée chez vous.</p>
    <a href="#" class="btn-cta">Découvrir Tunoova</a>
</section>
```

```css
.hero {
    background: linear-gradient(135deg, #453868, #5C4B87);
    border-radius: 20px;
    position: relative;
    overflow: hidden;
    padding: 3rem 2.5rem;
}
.hero h1 {
    font-family: 'Fredoka', sans-serif;
    font-weight: 700;
    font-size: clamp(2.5rem, 6vw, 4.5rem);
    line-height: 1.05;
    color: #F5E9D3;
    animation: slideUp 0.8s ease-out;
}
.hero h1 em { font-style: normal; color: #F2B33D; }
.hero p { color: rgba(255, 255, 255, 0.85); font-size: 1.1rem; line-height: 1.65; }
.hero .deco { position: absolute; opacity: 0.12; width: 260px; height: 260px; right: -60px; top: -80px; }
```

### Boutons

- **CTA orange** : l'action principale, une seule par écran
- **Violet plein** : action secondaire
- **Contour violet** : action tertiaire
- **Crème** : sur fond violet

```css
.btn-cta {
    display: inline-flex;
    align-items: center;
    padding: 0.95rem 2.2rem;
    background: #E8632A;
    color: #fff;
    font-family: 'Nunito', sans-serif;
    font-weight: 800;
    font-size: 1.05rem;
    border-radius: 999px;
    border: none;
    text-decoration: none;
    box-shadow: 0 8px 22px rgba(232, 99, 42, 0.35);
    transition: all 0.25s;
}
.btn-cta:hover { background: #C9501C; transform: translateY(-2px); box-shadow: 0 12px 28px rgba(232, 99, 42, 0.45); }

.btn-primary { background: #5C4B87; color: #fff; border-radius: 999px; padding: 0.8rem 1.8rem; font-weight: 700; }
.btn-primary:hover { background: #453868; transform: translateY(-2px); }

.btn-outline { background: transparent; color: #5C4B87; border: 2px solid #5C4B87; border-radius: 999px; padding: 0.75rem 1.8rem; font-weight: 700; }
.btn-outline:hover { background: #5C4B87; color: #fff; }

.btn-creme { background: #F5E9D3; color: #453868; border-radius: 999px; padding: 0.8rem 1.8rem; font-weight: 800; }
.btn-creme:hover { background: #fff; }
```

### Badges

Version teintée : fond pâle et texte foncé de la même famille.

```css
.badge { display: inline-block; padding: 0.35rem 0.9rem; border-radius: 999px; font-family: 'Nunito', sans-serif; font-weight: 800; font-size: 0.78rem; }

.badge-violet { background: #EDE9F5; color: #453868; }
.badge-orange { background: #FCE6DC; color: #A8431A; }
.badge-jaune  { background: #FDF0D5; color: #8A5F0E; }
.badge-vert   { background: #DDEFE3; color: #276B3D; }
.badge-bleu   { background: #DCE8F2; color: #1F5079; }
.badge-plein  { background: #5C4B87; color: #fff; }
```

### Titre de section

```css
.section-title {
    font-family: 'Fredoka', sans-serif;
    font-weight: 600;
    font-size: clamp(2rem, 4.5vw, 2.8rem);
    text-align: center;
}
.section-title::after {
    content: '';
    display: block;
    width: 90px; height: 5px;
    background: linear-gradient(90deg, #E8632A, #F2B33D, #3E9B5C, #2D6E9E);
    margin: 0.9rem auto 0;
    border-radius: 999px;
}
```

### Card fonctionnalité

```html
<div class="card">
    <div class="card-icon">🎵</div>
    <h3 class="card-title">Votre bibliothèque</h3>
    <p class="card-text">Tous vos MP3, classés par artiste et album, avec leurs pochettes.</p>
</div>
```

```css
.card {
    background: #FFFFFF;
    border: 1px solid #E5E1EC;
    border-radius: 20px;
    padding: 1.75rem;
    box-shadow: 0 4px 20px rgba(31, 27, 46, 0.06);
    transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
}
.card:hover {
    transform: translateY(-6px);
    border-color: #9C8AD6;
    box-shadow: 0 16px 40px rgba(92, 75, 135, 0.18);
}
/* Pastille d'icône : fond pâle d'une couleur d'accent (#FCE6DC, #FDF0D5, #DDEFE3, #DCE8F2) */
.card-icon  { width: 52px; height: 52px; border-radius: 14px; background: #FCE6DC; display: flex; align-items: center; justify-content: center; font-size: 1.6rem; margin-bottom: 1.1rem; }
.card-title { font-family: 'Fredoka', sans-serif; font-weight: 600; font-size: 1.35rem; margin-bottom: 0.5rem; }
.card-text  { color: #6B6478; font-size: 0.93rem; line-height: 1.6; }
```

### Card tarif

```css
.price-card          { background: #fff; border: 2px solid #E5E1EC; border-radius: 20px; padding: 2rem 1.75rem; text-align: center; }
.price-card.featured { border-color: #5C4B87; box-shadow: 0 16px 40px rgba(92, 75, 135, 0.18); }
.price-value         { font-family: 'Fredoka', sans-serif; font-weight: 700; font-size: 2.8rem; color: #5C4B87; }
.price-card li::before { content: '✓'; color: #3E9B5C; font-weight: 800; margin-right: 0.6rem; }
```

### Champ de saisie

```css
.input {
    background: #FFFFFF;
    border: 2px solid #E5E1EC;
    border-radius: 12px;
    color: #1F1B2E;
    font-family: 'Nunito', sans-serif;
    font-size: 0.95rem;
    padding: 0.75rem 1rem;
    outline: none;
    transition: all 0.2s;
}
.input::placeholder { color: #A79FB8; }
.input:focus { border-color: #5C4B87; box-shadow: 0 0 0 4px rgba(92, 75, 135, 0.15); }
```

### Footer

```css
footer             { background: #453868; padding: 1.5rem 2rem; text-align: center; }
footer p, footer a { color: rgba(245, 233, 211, 0.75); font-size: 0.9rem; text-decoration: none; }
footer a:hover     { color: #F2B33D; }
```

---

## 9. Mode sombre

Ce sont les valeurs exactes de l'application (`html[data-theme="dark"]`). Le violet s'éclaircit pour rester lisible, et les couleurs d'accent ne changent pas.

| Rôle | Clair | Sombre |
|---|---|---|
| Fond | `#FAFAFC` | `#17151F` |
| Surface | `#FFFFFF` | `#211E2C` |
| Bordure | `#E5E1EC` | `#3A3548` |
| Marque | `#5C4B87` | `#8A7CC4` |
| Marque survol | `#453868` | `#A79AE0` |
| Texte | `#1F1B2E` | `#F0EEF7` |
| Texte discret | `#6B6478` | `#A79FB8` |

---

## 10. Animations

| Animation | Effet | Durée | Usage |
|---|---|---|---|
| `spin` | rotation 360° | 12s, linear infini | Anneau décoratif |
| `equalizer` | `scaleY` 0.3→1 | 1s, infini (décalé) | Indicateur « en lecture » |
| `slideUp` | `translateY(24px)→0` + fondu | 0.8s | Hero H1 |
| `fadeIn` | `opacity 0→1` | 0.8s | Sous-titre, CTA du hero |
| `bounce` | `translateY(-8px)` | 2s, infini | Mascotte, flèche « défiler » |

```css
@keyframes spin {
    to { transform: rotate(360deg); }
}
@keyframes slideUp {
    from { opacity: 0; transform: translateY(24px); }
    to   { opacity: 1; transform: translateY(0); }
}
@keyframes fadeIn {
    from { opacity: 0; }
    to   { opacity: 1; }
}
@keyframes bounce {
    0%, 100% { transform: translateY(0); }
    50%      { transform: translateY(-8px); }
}
@keyframes equalizer {
    0%, 100% { transform: scaleY(0.3); }
    50%      { transform: scaleY(1); }
}

/* Transitions UI */
transition: all 0.25s;                                     /* standard */
transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);    /* card hover (léger rebond) */

/* Respect des préférences utilisateur */
@media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation: none !important; transition: none !important; }
}
```

### Exemple — application sur des éléments

```css
.hero h1          { animation: slideUp 0.8s ease-out; }
.hero p           { animation: fadeIn 0.8s ease-out 0.2s both; }
.hero .btn-cta    { animation: fadeIn 0.8s ease-out 0.4s both; }
.ring.deco        { animation: spin 12s linear infinite; }
.mascotte         { animation: bounce 2s ease-in-out infinite; }
.eq span          { transform-origin: bottom; animation: equalizer 1s ease-in-out infinite; }
```

---

## 11. Variables CSS — copier/coller

```css
:root {
    /* Marque */
    --violet:       #5C4B87;
    --violet-dark:  #453868;
    --violet-mid:   #7A64B8;
    --violet-light: #9C8AD6;
    --lavande:      #EDE9F5;
    --creme:        #F5E9D3;

    /* Accents */
    --orange: #E8632A;
    --orange-dark: #C9501C;   /* survol CTA, bouton orange < 19px (contraste AA) */
    --jaune:  #F2B33D;
    --vert:   #3E9B5C;
    --bleu:   #2D6E9E;

    /* Neutres */
    --bg:         #FAFAFC;
    --surface:    #FFFFFF;
    --text:       #1F1B2E;
    --text-muted: #6B6478;
    --border:     #E5E1EC;

    /* Fonctionnel */
    --error: #B3261E;
    --notif: #E3342F;

    /* Dégradés */
    --gradient-rainbow: linear-gradient(90deg, #E8632A, #F2B33D, #3E9B5C, #2D6E9E);
    --gradient-violet:  linear-gradient(135deg, #5C4B87, #7A64B8);

    /* Formes */
    --radius-sm: 8px;
    --radius-md: 12px;
    --radius-lg: 16px;
    --radius-xl: 20px;
    --radius-pill: 999px;
    --shadow-card:       0 4px 20px rgba(31, 27, 46, 0.06);
    --shadow-card-hover: 0 16px 40px rgba(92, 75, 135, 0.18);
    --shadow-cta:        0 8px 22px rgba(232, 99, 42, 0.35);

    --font-display: 'Fredoka', sans-serif;
    --font-body:    'Nunito', sans-serif;
}

[data-theme="dark"] {
    --violet:      #8A7CC4;
    --violet-dark: #A79AE0;
    --bg:          #17151F;
    --surface:     #211E2C;
    --text:        #F0EEF7;
    --text-muted:  #A79FB8;
    --border:      #3A3548;
}

body {
    font-family: var(--font-body);
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
}
h1, h2, h3 { font-family: var(--font-display); }
```
