# Regles fiscales utilisees par rendement.py

Lues sur les pages officielles le 2026-10-11. Chaque regle porte son identifiant (R1 a R9),
repris dans la sortie du script. Rien ici n'est un conseil : si une page a change depuis la
date de lecture, la page fait foi, pas ce fichier. Les citations sont reproduites entre
guillemets, accents retires dans ce fichier, texte original sur la page citee.

## R1. Micro-foncier : 15 000 EUR et abattement de 30 %

- Source : CGI art. 32, version en vigueur depuis le 21/02/2026,
  https://www.legifrance.gouv.fr/codes/id/LEGISCTA000006197178/ (section articles 28 a 32),
  lu le 2026-10-11.
- Citation : « Par derogation aux dispositions de l'article 31, lorsque le montant du revenu
  brut annuel defini aux articles 29 et 30 n'excede pas 15 000 EUR, le revenu imposable
  correspondant est fixe a une somme egale au montant de ce revenu brut diminue d'un
  abattement de 30 %. »
- Confirmation : service-public.fr fiche F1991, « verifie le 15/04/2026 »,
  https://www.service-public.fr/particuliers/vosdroits/F1991, lue le 2026-10-11 : plafond
  15 000 EUR, abattement 30 %, option pour le reel irrevocable trois ans.
- Dans le script : `MICRO_FONCIER_CEILING`, `MICRO_FONCIER_ABATEMENT`. Au-dela du plafond,
  le script refuse (`not_eligible`, R1) et ne calcule rien.

## R2. Regime reel des revenus fonciers : charges deductibles

- Source : CGI art. 31 I 1, version en vigueur du 21/02/2026 au 01/01/2027, meme page
  Legifrance que R1, lu le 2026-10-11.
- Citations : a) « Les depenses de reparation et d'entretien effectivement supportees par le
  proprietaire » ; a bis) les primes d'assurance ; b) « Les depenses d'amelioration afferentes
  aux locaux d'habitation, a l'exclusion des frais correspondant a des travaux » de
  construction, reconstruction ou agrandissement ; c) « Les impositions, autres que celles
  incombant normalement a l'occupant, percues, a raison desdites proprietes » ; d) « Les
  interets de dettes contractees pour la conservation, l'acquisition, la construction, la
  reparation » ou l'amelioration ; e) les frais de gestion.
- Confirmation : fiche F1991 (liste des charges deductibles au reel).
- Dans le script : charges d'exploitation, taxe fonciere, assurance, frais de gestion,
  interets d'emprunt, et les travaux de l'annee 1 si `works_deductible` est vrai. Les travaux
  de construction ou d'agrandissement ne sont pas deductibles : mettre `works_deductible`
  a faux.

## R3. Deficit foncier : 10 700 EUR sur le revenu global, le reste sur dix ans

- Source : CGI art. 156 I 3, version en vigueur depuis le 01/07/2026,
  https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000006307920, lu le 2026-10-11.
- Citations : « Des deficits fonciers, lesquels s'imputent exclusivement sur les revenus
  fonciers des dix annees suivantes » ; pour les deficits autres que les interets d'emprunt,
  imputables sur le revenu global : « L'imputation est limitee a 10 700 EUR » ; « La fraction
  du deficit superieure a 10 700 EUR et la fraction du deficit non imputable resultant des
  interets d'emprunt » s'imputent sur les revenus fonciers des dix annees suivantes ; plafond
  releve pour les travaux de renovation energetique « sans pouvoir exceder 21 400 EUR par
  an », classement a justifier au plus tard le 31/12/2027 ; « Lorsque le proprietaire cesse
  de louer un immeuble », le revenu foncier et le revenu global « des trois annees qui
  precedent » sont reconstitues.
- Decret d'application du plafond de 21 400 EUR : decret 2023-297 du 21/04/2023,
  https://www.legifrance.gouv.fr/jorf/id/JORFTEXT000047476729, cite par la fiche F1991.
- Confirmation : fiche F1991 (10 700 EUR hors interets, report dix ans, 21 400 EUR jusqu'au
  31/12/2027).
- Dans le script : `DEFICIT_FONCIER_GLOBAL_CAP`, `DEFICIT_FONCIER_ENERGY_CAP`
  (`energy_renovation` a vrai), `DEFICIT_CARRY_YEARS`. Le script suppose que le revenu
  global absorbe la deduction (hypothese A3) et ne verifie pas la condition de location de
  trois ans (A8).

## R4. Prelevements sociaux : 17,2 %

- Sources : fiches F1991 et F32744, « verifie le 15/04/2026 », lues le 2026-10-11 : les
  revenus fonciers et les revenus de location meublee non professionnelle supportent les
  prelevements sociaux au taux de 17,2 %.
- Dans le script : `SOCIAL_RATE`, applique au revenu imposable positif. La CSG deductible
  de l'annee suivante n'est pas modelisee (A5).

## R5. Micro-BIC des locations meublees : plafonds et abattements

- Source : CGI art. 50-0, https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000042159220/,
  lu le 2026-10-11 ; fiche F32744, https://www.service-public.fr/particuliers/vosdroits/F32744,
  « verifie le 15/04/2026 », lue le 2026-10-11 ; BOFiP ACTU-2025-00127 du 19/08/2026,
  https://bofip.impots.gouv.fr/bofip/14765-PGP.html, lu le 2026-10-11.
- Valeurs lues : revenus 2025, location meublee de longue duree, recettes au plus 77 700 EUR
  et abattement de 50 % ; revenus 2026, 83 600 EUR et 50 % pour toutes les locations
  meublees sauf les meubles de tourisme non classes : 15 000 EUR et 30 %. Meubles de tourisme
  classes et chambres d'hotes : 77 700 EUR (2025) puis 83 600 EUR (2026), abattement 50 %.
- Dans le script : `MICRO_BIC_LONG_TERM` par annee de revenus, `MICRO_BIC_TOURISM_UNCLASSIFIED`.
  Au-dela du plafond, refus (`not_eligible`, R5). Pour une annee de revenus posterieure a
  2026, le plafond 2026 est repris et un avertissement est ecrit.

## R6. Statut de loueur en meuble non professionnel

- Source : CGI art. 155 IV, https://www.legifrance.gouv.fr/codes/id/LEGISCTA000006197230/,
  lu le 2026-10-11 ; fiche F32744.
- Regle lue : le loueur est non professionnel si les recettes annuelles ne depassent pas
  23 000 EUR, ou si elles ne depassent pas les autres revenus d'activite du foyer.
- Dans le script : `LMNP_STATUS_CEILING` ; au-dela de 23 000 EUR, avertissement, le script
  ne connait pas les autres revenus du foyer.

## R7. Amortissement plafonne au loyer moins les autres charges

- Source : CGI art. 39 C II 2 et 3, version en vigueur depuis le 01/01/2015,
  https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000029355753, lu le 2026-10-11.
- Citations : l'amortissement est admis « dans la limite du montant du loyer acquis [...]
  diminue du montant des autres charges afferentes a ces biens ou parts » ; l'excedent
  « peut etre deduit du resultat des exercices suivants ».
- Commentaire : BOFiP BOI-BIC-AMT-20-40-10-20 du 01/03/2017,
  https://bofip.impots.gouv.fr/bofip/4527-PGP.html, lu le 2026-10-11, paragraphes 40 (limite),
  60 (charges retenues : entretien, reparation, assurance, interets, taxes foncieres),
  70 (frais de comptabilite non retenus), 80 et 90 (report de l'excedent).
- Dans le script : `cap = max(0, loyer - charges)`, excedent reporte sans limite de duree
  dans `depreciation_carried`.

## R8. Deficits LMNP : dix ans sur les revenus de meme nature

- Source : CGI art. 156 I 1 ter, meme page que R3, lu le 2026-10-11.
- Citations : « Des deficits du foyer fiscal provenant de l'activite de location directe ou
  indirecte de locaux d'habitation meubles », « lorsque l'activite n'est pas exercee a titre
  professionnel au sens du IV de l'article 155 » ; « Ces deficits s'imputent exclusivement
  sur les revenus provenant d'une telle activite » des dix annees suivantes.
- Commentaire : BOFiP BOI-BIC-CHAMP-40-20 du 19/08/2026,
  https://bofip.impots.gouv.fr/bofip/3610-PGP.html, lu le 2026-10-11, paragraphes 240 et 250.
- Dans le script : `bic_carry`, dix ans, uniquement sur le resultat positif de la meme
  activite.

## R9. Bareme de l'impot sur le revenu, revenus 2025

- Source : fiche F1419, https://www.service-public.fr/particuliers/vosdroits/F1419,
  « verifie le 15/04/2026 », lue le 2026-10-11, qui renvoie aux articles 193 a 199 du CGI.
- Valeurs lues : 0 % jusqu'a 11 600 EUR ; 11 % de 11 601 a 29 579 EUR ; 30 % de 29 580 a
  84 577 EUR ; 41 % de 84 578 a 181 917 EUR ; 45 % au-dela.
- Dans le script : la tranche marginale est une entree (`tmi`) choisie parmi ces cinq taux ;
  le script ne calcule pas l'impot du foyer.

## Ce que le script ne couvre pas

SCI a l'impot sur les societes, demembrement, dispositifs de reduction d'impot (Pinel,
Denormandie, Loc'Avantages), plus-value a la revente, IFI, cotisations sociales des loueurs
professionnels, CFE. Pour ces cas, le script ne doit pas etre utilise comme s'il les couvrait.
