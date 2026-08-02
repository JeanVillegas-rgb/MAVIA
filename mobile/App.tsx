import AppNavigator
from "./src/navigation/AppNavigator";

import { LessonProvider }
from "./src/context/LessonContext";

export default function App() {

    return (

        <LessonProvider>

            <AppNavigator/>

        </LessonProvider>

    );

}
