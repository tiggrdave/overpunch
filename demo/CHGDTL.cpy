000100* --------------------------------------------------------------
000200* CHARGE DETAIL RECORD - SYNTHETIC. Generated for demonstration.
000300* --------------------------------------------------------------
000400 01  CHARGE-DETAIL-RECORD.
000500     05  CHG-ACCOUNT-ID        PIC 9(09).
000600     05  CHG-EMPLOYER-NO       PIC S9(07).
000700     05  CHG-EFFECTIVE-DATE.
000800         10  CHG-CC            PIC 9(02).
000900         10  CHG-YY            PIC 9(02).
001000         10  CHG-MM            PIC 9(02).
001100         10  CHG-DD            PIC 9(02).
001200     05  CHG-BENEFIT-AMT       PIC S9(08)V99.
001300     05  CHG-ADJUSTMENT-AMT    PIC S9(08)V99.
001400     05  CHG-GROSS-AMT         PIC 9(08)V99.
001500     05  CHG-PROGRAM-CODE      PIC X(01).
001600         88  PGM-STANDARD      VALUE 'S'.
001700         88  PGM-EXTENDED      VALUE 'R'.
001800         88  PGM-REIMBURSABLE  VALUE 'R'.
001900     05  CHG-QUARTER-CNT       PIC S9(04) COMP.
002000     05  CHG-PACKED-TOTAL      PIC S9(07)V99 COMP-3.
002100     05  FILLER                PIC X(10).
