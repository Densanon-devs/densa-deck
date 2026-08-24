/**
 * The entry point Android actually starts from.
 *
 * `registerRootComponent` is what hands the component tree to the native host
 * and sets up the development client. Without this file the native build fails
 * at configuration time with "Cannot convert '' to File", which says nothing
 * about a missing entry point.
 */
import { registerRootComponent } from 'expo';

import App from './App';

registerRootComponent(App);
