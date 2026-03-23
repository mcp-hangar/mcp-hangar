import type { Variants, Transition } from 'framer-motion'

/**
 * Shared framer-motion variants and transitions for consistent animation
 * across the entire UI. Keep durations short (150-300ms) for snappy feel.
 */

/* ------------------------------------------------------------------ */
/*  Transitions                                                        */
/* ------------------------------------------------------------------ */

export const snappy: Transition = {
  type: 'spring',
  stiffness: 500,
  damping: 30,
}

export const gentle: Transition = {
  type: 'spring',
  stiffness: 300,
  damping: 25,
}

export const quick: Transition = {
  duration: 0.15,
  ease: [0.25, 0.1, 0.25, 1],
}

/* ------------------------------------------------------------------ */
/*  Page / section entrance                                            */
/* ------------------------------------------------------------------ */

export const pageVariants: Variants = {
  hidden: { opacity: 0, y: 8, filter: 'blur(4px)' },
  visible: {
    opacity: 1,
    y: 0,
    filter: 'blur(0px)',
    transition: { duration: 0.25, ease: [0.25, 0.1, 0.25, 1] },
  },
  exit: {
    opacity: 0,
    y: -4,
    filter: 'blur(4px)',
    transition: { duration: 0.15 },
  },
}

/* ------------------------------------------------------------------ */
/*  Staggered children (for lists, grids, card groups)                 */
/* ------------------------------------------------------------------ */

export const staggerContainer: Variants = {
  hidden: { opacity: 1 },
  visible: {
    opacity: 1,
    transition: {
      staggerChildren: 0.04,
      delayChildren: 0.02,
    },
  },
}

export const staggerItem: Variants = {
  hidden: { opacity: 0, y: 8 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.2, ease: [0.25, 0.1, 0.25, 1] },
  },
}

/* ------------------------------------------------------------------ */
/*  Card hover                                                         */
/* ------------------------------------------------------------------ */

export const cardHover = {
  rest: { y: 0, boxShadow: '0 1px 2px 0 rgba(0,0,0,0.04)' },
  hover: {
    y: -2,
    boxShadow: '0 8px 24px -4px rgba(0,0,0,0.08), 0 2px 6px -2px rgba(0,0,0,0.04)',
    transition: quick,
  },
  tap: { y: 0, scale: 0.995, transition: { duration: 0.1 } },
}

/* ------------------------------------------------------------------ */
/*  Drawer / panel slide                                               */
/* ------------------------------------------------------------------ */

export const drawerVariants: Variants = {
  hidden: { x: '100%', opacity: 0.5 },
  visible: {
    x: 0,
    opacity: 1,
    transition: { ...gentle, stiffness: 400 },
  },
  exit: {
    x: '100%',
    opacity: 0.5,
    transition: { duration: 0.2, ease: [0.4, 0, 1, 1] },
  },
}

export const overlayVariants: Variants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { duration: 0.2 } },
  exit: { opacity: 0, transition: { duration: 0.15 } },
}

/* ------------------------------------------------------------------ */
/*  Modal / dialog scale                                               */
/* ------------------------------------------------------------------ */

export const modalVariants: Variants = {
  hidden: { opacity: 0, scale: 0.96, y: 8 },
  visible: {
    opacity: 1,
    scale: 1,
    y: 0,
    transition: { ...snappy, stiffness: 400 },
  },
  exit: {
    opacity: 0,
    scale: 0.96,
    y: 8,
    transition: { duration: 0.15 },
  },
}

/* ------------------------------------------------------------------ */
/*  Button press                                                       */
/* ------------------------------------------------------------------ */

export const buttonTap = { scale: 0.97 }
export const buttonHover = { scale: 1.01 }

/* ------------------------------------------------------------------ */
/*  List row (subtle slide-in for table rows / list items)             */
/* ------------------------------------------------------------------ */

export const listRowVariants: Variants = {
  hidden: { opacity: 0, x: -6 },
  visible: {
    opacity: 1,
    x: 0,
    transition: { duration: 0.18, ease: [0.25, 0.1, 0.25, 1] },
  },
}

/* ------------------------------------------------------------------ */
/*  Expand / collapse                                                  */
/* ------------------------------------------------------------------ */

export const expandVariants: Variants = {
  hidden: { opacity: 0, height: 0 },
  visible: {
    opacity: 1,
    height: 'auto',
    transition: { duration: 0.2, ease: [0.25, 0.1, 0.25, 1] },
  },
  exit: {
    opacity: 0,
    height: 0,
    transition: { duration: 0.15 },
  },
}

/* ------------------------------------------------------------------ */
/*  Fade in (simple)                                                   */
/* ------------------------------------------------------------------ */

export const fadeIn: Variants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { duration: 0.2 } },
}

/* ------------------------------------------------------------------ */
/*  Skeleton shimmer keyframes are in index.css                        */
/* ------------------------------------------------------------------ */
