000100* --------------------------------------------------------------
000200* UTILITY BILLING DETAIL RECORD - SYNTHETIC.
000300* Invented for demonstration. Not derived from any real system.
000400* --------------------------------------------------------------
000500 01  BILLING-DETAIL-RECORD.
000600     05  BIL-ACCOUNT-NO        PIC 9(09).
000700     05  BIL-METER-ID          PIC S9(07).
000800     05  BIL-READ-DATE.
000900         10  BIL-CC            PIC 9(02).
001000         10  BIL-YY            PIC 9(02).
001100         10  BIL-MM            PIC 9(02).
001200         10  BIL-DD            PIC 9(02).
001300     05  BIL-CHARGE-AMT        PIC S9(08)V99.
001400     05  BIL-ADJUSTMENT-AMT    PIC S9(08)V99.
001500     05  BIL-BALANCE-AMT       PIC 9(08)V99.
001600     05  BIL-RATE-CLASS        PIC X(01).
001700         88  RATE-RESIDENTIAL  VALUE 'R'.
001800         88  RATE-COMMERCIAL   VALUE 'C'.
001900         88  RATE-RELIEF       VALUE 'R'.
002000     05  BIL-CYCLE-CNT         PIC S9(04) COMP.
002100     05  BIL-PACKED-TOTAL      PIC S9(07)V99 COMP-3.
002200     05  FILLER                PIC X(10).
