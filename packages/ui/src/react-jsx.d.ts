// Re-export React.JSX as global JSX namespace.
// Required since @types/react@19.2 removed the global JSX namespace.
// See: https://github.com/DefinitelyTyped/DefinitelyTyped/pull/71924

import type React from "react";

declare global {
  namespace JSX {
    type Element = React.JSX.Element;
    type IntrinsicElements = React.JSX.IntrinsicElements;
    type ElementChildrenAttribute = React.JSX.ElementChildrenAttribute;
  }
}
