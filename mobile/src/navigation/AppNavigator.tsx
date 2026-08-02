import { NavigationContainer } from "@react-navigation/native";
import { createNativeStackNavigator } from "@react-navigation/native-stack";

import HomeScreen from "../screens/HomeScreen";
import LessonScreen from "../screens/LessonScreen";
import QuestionScreen from "../screens/QuestionScreen";
import CompletionScreen from "../screens/CompletionScreen"; 

const Stack = createNativeStackNavigator();

export default function AppNavigator() {
  return (
    <NavigationContainer>
      <Stack.Navigator
        initialRouteName="Home"
        screenOptions={{ headerShown: false }}
      >

        <Stack.Screen
          name="Home"
          component={HomeScreen}
        />

        <Stack.Screen
          name="Lesson"
          component={LessonScreen}
        />

        <Stack.Screen
          name="Question"
          component={QuestionScreen}
        />

        {
        <Stack.Screen
          name="Completion"
          component={CompletionScreen}
        />
        }

      </Stack.Navigator>
    </NavigationContainer>
  );
}
