/** The server's default MAX_MESSAGE_CHARS. The server stays the authority: a different setting only
 *  costs a 413, which is handled like any refusal (docs/PHASE4.md, "Errors and limits"). */
export const MAX_MESSAGE_CHARS = 4000;

/** The counter appears when a message gets this close to the limit. */
export const COUNTER_FROM = 3500;
