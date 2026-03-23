import { motion } from 'framer-motion'
import { pageVariants, staggerContainer } from '../../lib/animations'
import { cn } from '../../lib/cn'

interface PageContainerProps {
  children: React.ReactNode
  className?: string
}

/**
 * Wraps every page with a smooth fade-up entrance animation
 * and stagger-container for child elements.
 */
export function PageContainer({ children, className }: PageContainerProps): JSX.Element {
  return (
    <motion.div
      variants={{ ...pageVariants, ...staggerContainer }}
      initial="hidden"
      animate="visible"
      exit="exit"
      className={cn('w-full', className)}
    >
      {children}
    </motion.div>
  )
}
